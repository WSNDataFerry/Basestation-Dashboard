const state = {
    nodes: new Map(), // Map of nodeId -> node data
    charts: new Map(), // Map of nodeId -> Chart.js instance
    config: {}, // Store GPS & Naming configuration from backend
    lastUpdated: null,
    activeView: 'overview', // 'overview' or 'detail'
    activeCluster: null, // cluster ID string/number
    map: null,
    mapLayerGroups: new Map(),
    mapHasFitBounds: false
    ,
    drones: new Map(), // id -> { positions: [{lat,lng,ts}], marker, polyline }
    droneLayerGroup: null
    ,
    // Waypoint support
    waypoints: [], // ordered array of {id, lat, lng}
    waypointLayerGroup: null,
    waypointMode: false,
    nodeMarkers: new Map()
};
// CH edit mode: when true, single-clicking a node toggles its CH role locally
state.chEditMode = false;

// Configuration
const CHART_COLORS = {
    temp: 'rgb(239, 68, 68)', // Red
    hum: 'rgb(59, 130, 246)',  // Blue
    aqi: 'rgb(16, 185, 129)',  // Green
    eco2: 'rgb(245, 158, 11)'  // Amber
};

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    initDashboard();

    // Live updates are handled by SSE; no polling/refresh button required.

    // Save/Edit CHs button toggles edit mode; clicking again saves
    const saveBtn = document.getElementById('save-chs-btn');
    if (saveBtn) saveBtn.addEventListener('click', () => toggleCHEditMode());

    // Start mission button
    const startBtn = document.getElementById('start-mission-btn');
    if (startBtn) startBtn.addEventListener('click', () => openMissionEditor());

    // Back button
    document.getElementById('back-btn').addEventListener('click', () => {
        switchView('overview');
    });

    // Sidebar home button
    document.querySelectorAll('.nav-item[data-view]').forEach(el => el.addEventListener('click', () => {
        const view = el.dataset.view;
        switchView(view);
    }));

    // Sidebar toggle
    const sidebarToggle = document.getElementById('sidebar-toggle');
    if (sidebarToggle) sidebarToggle.addEventListener('click', () => {
        const sb = document.querySelector('.sidebar');
        if (sb) sb.classList.toggle('collapsed');
    });

    // Waypoint buttons
    const createWp = document.getElementById('create-waypoint-btn');
    const startWp = document.getElementById('start-waypoint-btn');
    const clearWp = document.getElementById('clear-waypoints-btn');
    if (createWp) createWp.addEventListener('click', () => {
        state.waypointMode = !state.waypointMode;
        createWp.classList.toggle('active', state.waypointMode);
        if (!state.waypointMode) {
            // exiting mode — leave current selections
            console.log('Waypoint mode disabled');
        } else {
            console.log('Waypoint mode enabled — click cluster-head nodes in desired order');
        }
    });
    if (startWp) startWp.addEventListener('click', () => {
        // Start waypoint — left blank for later implementation
        console.log('Start Waypoint pressed (not implemented)');
    });
    if (clearWp) clearWp.addEventListener('click', () => {
        clearWaypoints();
    });
});

// Debug overlay removed — no-op in production UI

async function initDashboard() {
    initMap();
    await fetchConfig();
    await fetchTelemetryData();
    // Start SSE connection for real-time updates
    initSSE();
}

// Toggle CH edit mode: enter to allow single-click toggles, exit to save
async function toggleCHEditMode() {
    const btn = document.getElementById('save-chs-btn');
    if (!state.chEditMode) {
        // Enter edit mode
        state.chEditMode = true;
        if (btn) {
            btn.textContent = 'Save CHs';
            btn.classList.add('active');
        }
        console.log('Entered CH edit mode — click nodes to toggle CH status, then click Save CHs to persist.');
        return;
    }

    // Exiting edit mode: attempt to save changes
    const ok = await saveCHs();
    if (ok) {
        state.chEditMode = false;
        if (btn) {
            btn.textContent = 'Edit CHs';
            btn.classList.remove('active');
        }
        console.log('CH selections saved and edit mode exited.');
    } else {
        // keep editing if save failed
        console.warn('Failed to save CHs; remaining in edit mode.');
    }
}

// Waypoint selection helpers
function handleWaypointSelect(nodeId, lat, lng) {
    if (!state.waypointMode) return;
    if (lat == null || lng == null) {
        console.warn('Selected node has no GPS coordinates');
        return;
    }
    // Preserve click order
    state.waypoints.push({ id: nodeId, lat: lat, lng: lng });
    console.log('Waypoint added', nodeId);
    renderWaypoints();
}

function renderWaypoints() {
    if (!state.waypointLayerGroup) return;
    state.waypointLayerGroup.clearLayers();

    const latlngs = [];
    // start from active drone if present
    let droneStart = null;
    for (const [did, info] of state.drones.entries()) {
        if (info.positions && info.positions.length > 0) {
            const last = info.positions[info.positions.length - 1];
            droneStart = [last.lat, last.lng];
            break;
        }
    }
    if (droneStart) latlngs.push(droneStart);

    state.waypoints.forEach(wp => latlngs.push([wp.lat, wp.lng]));

    if (latlngs.length > 1) {
        L.polyline(latlngs, { color: '#ffd54f', weight: 3, opacity: 0.95 }).addTo(state.waypointLayerGroup);
    }

    // Add markers for each waypoint in order
    state.waypoints.forEach((wp, idx) => {
        const m = L.circleMarker([wp.lat, wp.lng], { radius: 6, fillColor: '#f59e0b', color: '#fff', weight: 1 }).addTo(state.waypointLayerGroup);
        m.bindTooltip(`${idx+1}. ${wp.id}`, { permanent: true, direction: 'right' });
    });
}

function clearWaypoints() {
    state.waypoints = [];
    if (state.waypointLayerGroup) state.waypointLayerGroup.clearLayers();
    console.log('Waypoints cleared');
}

function initSSE() {
    if (typeof EventSource === 'undefined') return;
    try {
        const es = new EventSource('/events');
        es.onmessage = (e) => {
            try {
                const rec = JSON.parse(e.data);
                // If drone telemetry
                if (rec.type && rec.type === 'drone') {
                    updateDrones([rec]);
                    // update telemetry panel
                    setActiveDroneTelemetry(rec.id || rec.drone_id || 'drone', rec);
                } else if (rec.id) {
                    // node telemetry: append to state.nodes array
                    const nodeId = rec.id;
                    const arr = state.nodes.get(nodeId) || [];
                    arr.push(rec);
                    arr.sort((a,b)=> (a.ts||0)-(b.ts||0));
                    state.nodes.set(nodeId, arr);
                    // Update UI if necessary
                    updateMapFromState();
                    if (state.activeView === 'detail' && state.activeCluster) renderClusterDetail(state.activeCluster);
                }
            } catch (err) {
                console.error('SSE parse error', err);
            }
        };
        es.onerror = (err) => {
            console.error('SSE error', err);
        };
    } catch (e) {
        console.warn('EventSource not available', e);
    }
}

function updateMapFromState() {
    // Re-render map layers from current state.nodes content
    const clusters = new Set();
    state.nodes.forEach(records => { if (records.length>0 && records[records.length-1].cid) clusters.add(records[records.length-1].cid); });
    updateMap(clusters);
}

// Save current CH selections to the server
async function saveCHs() {
    try {
        const chs = [];
        Object.keys(state.config).forEach(k => {
            if (state.config[k] && state.config[k].is_ch) chs.push(k);
        });

        const resp = await fetch('/api/ch_update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chs })
        });
        const j = await resp.json();
        if (resp.ok) {
            // success — notify and keep UI consistent
            alert(`Saved ${chs.length} CH(s)`);
            return true;
        } else {
            alert(`Failed to save CHs: ${j.error || JSON.stringify(j)}`);
            return false;
        }
    } catch (e) {
        console.error('saveCHs error', e);
        alert('Error saving CHs (see console)');
        return false;
    }
}

// (removed) persistent silent CH save helper — CH persistence now handled by explicit Save action

// Start a waypoint mission using selected CHs. Builds a payload from state.config
async function startMission() {
    // legacy: now handled by modal; kept for compatibility
    openMissionEditor();
}

function openMissionEditor() {
    const selected = [];
    Object.keys(state.config).forEach(k => { if (state.config[k] && state.config[k].is_ch) selected.push(k); });
    if (selected.length === 0) return alert('No cluster heads selected. Toggle CH on markers and save CHs first.');

    const body = document.getElementById('mission-modal-body');
    body.innerHTML = '';

    selected.forEach(nodeId => {
        const cfg = state.config[nodeId] || {};
        const row = document.createElement('div');
        row.className = 'mission-node-row';
        row.innerHTML = `
            <h4>Node ${nodeId} - ${cfg.name || ''}</h4>
            <label>Latitude: <input class="mission-input" data-node="${nodeId}" data-field="gps_lat" value="${cfg.lat||''}"></label>
            <label>Longitude: <input class="mission-input" data-node="${nodeId}" data-field="gps_lon" value="${cfg.lng||''}"></label>
            <label>Height (m): <input class="mission-input" data-node="${nodeId}" data-field="height_from_the_ground" value="${cfg.default_height||5.0}" type="number" step="0.1"></label>
            <label>Hover: <input class="mission-input" data-node="${nodeId}" data-field="hover" type="checkbox" ${cfg.default_hover ? 'checked':''}></label>
            <label>RTL after mission: <input class="mission-input" data-node="${nodeId}" data-field="rtl" type="checkbox" ${cfg.default_rtl ? 'checked':''}></label>
            <label>Land after mission: <input class="mission-input" data-node="${nodeId}" data-field="land" type="checkbox" ${cfg.default_land ? 'checked':''}></label>
            <hr/>
        `;
        body.appendChild(row);
    });

    // Show modal
    const modal = document.getElementById('mission-modal');
    modal.classList.remove('hidden');

    // Wire buttons
    document.getElementById('mission-modal-close').onclick = closeMissionModal;
    document.getElementById('mission-cancel').onclick = closeMissionModal;
    document.getElementById('mission-launch').onclick = submitMissionFromModal;
}

function closeMissionModal() {
    document.getElementById('mission-modal').classList.add('hidden');
}

async function submitMissionFromModal() {
    // Collect inputs
    const inputs = Array.from(document.querySelectorAll('.mission-input'));
    const payload = {};
    const nodes = new Set();
    inputs.forEach(inp => {
        const node = inp.dataset.node;
        const field = inp.dataset.field;
        nodes.add(node);
        if (!payload[`node_${node}`]) payload[`node_${node}`] = {};
        if (inp.type === 'checkbox') payload[`node_${node}`][field] = inp.checked;
        else if (inp.type === 'number') payload[`node_${node}`][field] = parseFloat(inp.value);
        else payload[`node_${node}`][field] = inp.value === '' ? null : (isNaN(inp.value) ? inp.value : (inp.value.includes('.') ? parseFloat(inp.value) : parseInt(inp.value)));
    });

    // Validate GPS coords
    for (const key of Object.keys(payload)) {
        const p = payload[key];
        if (!p.gps_lat || !p.gps_lon) {
            if (!confirm(`${key} missing lat/lon. Continue anyway?`)) return;
        }
    }

    try {
        const resp = await fetch('/api/mission', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mission_type: 'waypoint', payload })
        });
        const j = await resp.json();
        if (resp.ok) {
            alert('Mission submitted: ' + JSON.stringify(j.detail || j));
            closeMissionModal();
        } else {
            alert('Mission failed: ' + (j.error || JSON.stringify(j)));
        }
    } catch (e) {
        console.error('submitMissionFromModal error', e);
        alert('Error submitting mission (see console)');
    }
}

// Drone layer initialization
function initDroneLayer() {
    if (!state.map) return;
    state.droneLayerGroup = L.layerGroup().addTo(state.map);
    // Layer group for waypoint visuals (lines / markers)
    state.waypointLayerGroup = L.layerGroup().addTo(state.map);
}

// Map Initialization
function initMap() {
    state.map = L.map('global-map').setView([0, 0], 2); // Default view, auto-fit later

    // Define multiple base layers
    const esriSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
        maxZoom: 19
    });

    const stamenWatercolor = L.tileLayer('https://stamen-tiles-{s}.a.ssl.fastly.net/watercolor/{z}/{x}/{y}.jpg', {
        attribution: 'Map tiles by Stamen Design, CC BY 3.0 &mdash; Map data &copy; OpenStreetMap contributors',
        subdomains: 'abcd',
        maxZoom: 16,
        tileSize: 256
    });

    // Fallback: if Stamen tiles fail to load (CORS/rate limits or cached bad URL), switch to Carto Voyager
    let watercolorTileErrorSeen = false;
    stamenWatercolor.on('tileerror', function (err) {
        if (watercolorTileErrorSeen) return;
        watercolorTileErrorSeen = true;
        console.warn('Stamen Watercolor tile load failed; switching to Street (Voyager) as fallback', err);
        try {
            state.currentBase && state.map.removeLayer(state.currentBase);
            cartoVoyager.addTo(state.map);
            state.currentBase = cartoVoyager;
        } catch (e) {
            console.error('Failed to switch base layer fallback', e);
        }
    });

    const cartoVoyager = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
        subdomains: 'abcd',
        maxZoom: 19
    });

    // Add default satellite layer
    esriSat.addTo(state.map);
    state.currentBase = esriSat;

    // Layer control (top-right) to switch skins
    const baseLayers = {
        'Satellite': esriSat,
        'Watercolor (cartoon)': stamenWatercolor,
        'Street (Voyager)': cartoVoyager
    };
    // Keep a reference to the control so we can position overlays relative to it
    const layerControl = L.control.layers(baseLayers, null, { position: 'topright', collapsed: false }).addTo(state.map);
    state.layerControl = layerControl;

    // Initialize drone layer and waypoint layer after base tile layer added
    initDroneLayer();

    // Position the telemetry panel so it sits directly under the layer control
    // and update on window/map changes.
    function positionTelemetryPanel() {
        try {
            const panel = document.getElementById('telemetry-panel');
            if (!panel || !state.map || !state.layerControl) return;
            const ctrl = state.layerControl.getContainer();
            if (!ctrl) return;
            const ctrlRect = ctrl.getBoundingClientRect();
            const mapRect = state.map.getContainer().getBoundingClientRect();

            // Compute top relative to map container
            const topPx = Math.max(6, Math.round(ctrlRect.bottom - mapRect.top + 6));
            // Compute right offset so panel aligns under the control's right edge
            const rightPx = Math.max(8, Math.round(mapRect.right - ctrlRect.right + 8));

            panel.style.position = 'absolute';
            panel.style.top = topPx + 'px';
            panel.style.right = rightPx + 'px';
            panel.style.left = 'auto';
        } catch (e) {
            console.debug('positionTelemetryPanel error', e);
        }
    }

    // Initial position and event bindings
    setTimeout(positionTelemetryPanel, 50);
    window.addEventListener('resize', positionTelemetryPanel);
    state.map.on && state.map.on('moveend', positionTelemetryPanel);
    state.map.on && state.map.on('baselayerchange', positionTelemetryPanel);
}

// Find a normalized config entry by node id. Handles variants like 'node_001', 'node_1', '001', or numeric ids
function findConfigById(nodeId) {
    if (!nodeId) return null;
    // direct match
    if (state.config[nodeId]) return state.config[nodeId];

    // Try common prefixes
    const asStr = String(nodeId);
    const tryKeys = [];
    // if nodeId already contains non-digits, try to extract digits
    const digitsMatch = asStr.match(/(\d+)/);
    const digits = digitsMatch ? digitsMatch[0] : asStr;

    // common variants: node_1001, node_001, 1001, 001, etc.
    tryKeys.push(`node_${digits}`);
    // If telemetry IDs include extra prefix (e.g. 1001) but config uses last-3 (e.g. node_001), try suffixes
    if (digits.length > 3) {
        const last3 = digits.slice(-3);
        tryKeys.push(`node_${last3}`);
        tryKeys.push(`node_${last3.padStart(3, '0')}`);
    }
    // zero pad to 3 if needed (node_001)
    if (digits.length < 3) tryKeys.push(`node_${digits.padStart(3, '0')}`);
    // also try plain digits and numeric form
    tryKeys.push(digits);
    tryKeys.push(String(parseInt(digits, 10)));

    for (const k of tryKeys) {
        if (k && state.config[k]) return state.config[k];
    }

    // Last resort: search config keys for one containing the digits
    for (const [k, v] of Object.entries(state.config)) {
        if (k.includes(digits) || (String(v && (v.name || '')).includes(digits))) return v;
    }

    return null;
}

async function fetchConfig() {
    try {
        const response = await fetch('/api/config');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        // Normalize config fields so the frontend can rely on consistent property names
        const raw = await response.json();
        const normalized = {};
        Object.entries(raw || {}).forEach(([k, v]) => {
            // handle a variety of naming conventions coming from nodes_config.json
            const lat = v.gps_lat ?? v.lat ?? v.latitude ?? v.lat_deg ?? null;
            const lng = v.gps_lon ?? v.lng ?? v.longitude ?? v.lon ?? null;
            const default_height = v.height_from_the_ground ?? v.default_height ?? v.height ?? 5.0;
            const default_hover = (v.hover === true) || (String(v.hover).toLowerCase() === 'true');
            const default_rtl = (v.rtl === true) || (String(v.rtl).toLowerCase() === 'true');
            const default_land = (v.land === true) || (String(v.land).toLowerCase() === 'true');
            const is_ch = (v.is_ch === true) || (String(v.is_ch).toLowerCase() === 'true');
            const name = v.name || v.label || k;

            normalized[k] = Object.assign({}, v, {
                lat: lat,
                lng: lng,
                default_height: default_height,
                default_hover: default_hover,
                default_rtl: default_rtl,
                default_land: default_land,
                is_ch: is_ch,
                name: name
            });
        });
        state.config = normalized;
        // Debug: show normalized config in console for troubleshooting and update overlay
    try { console.debug('Dashboard: loaded normalized config', Object.keys(state.config)); } catch(e) {}
    } catch (error) {
        console.error("Failed to fetch GPS config", error);
    }
}

// Data Fetching
async function fetchTelemetryData() {
    try {
        const response = await fetch('/api/data');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const result = await response.json();
        if (result.status === 'success') {
            processData(result.data);
            updateTimestamp();
        }
    } catch (error) {
        console.error("Failed to fetch telemetry data", error);
        // Optional: Show error state in UI
    }
}

// Data Processing
function processData(rawData) {
    if (!rawData || rawData.length === 0) return;

    // Group data by node ID and discover clusters
    const groupedData = new Map();
    const clustersSeen = new Set();

    // Collect drone telemetry separately
    const droneTelemetry = [];

    rawData.forEach(record => {
        // If this is drone telemetry, push to droneTelemetry and continue
        if (record.type && record.type === 'drone') {
            droneTelemetry.push(record);
            return;
        }

        const nodeId = record.id;
        if (!groupedData.has(nodeId)) {
            groupedData.set(nodeId, []);
        }
        groupedData.get(nodeId).push(record);
        if (record.cid) clustersSeen.add(record.cid);
    });

    // Sort each node's data by timestamp and update state
    groupedData.forEach((records, nodeId) => {
        records.sort((a, b) => a.ts - b.ts);
        state.nodes.set(nodeId, records);
    });

    // Update UI components
    renderSidebar(clustersSeen);
    updateMap(clustersSeen);

    // Update drone layer
    if (droneTelemetry.length > 0) {
        updateDrones(droneTelemetry);
    }

    if (state.activeView === 'overview') {
        renderOverview(clustersSeen);
    } else if (state.activeView === 'detail' && state.activeCluster) {
        renderClusterDetail(state.activeCluster);
    }
}

// Routing & View Management
function switchView(view, clusterId = null) {
    state.activeView = view;
    state.activeCluster = clusterId;

    const overviewView = document.getElementById('overview-view');
    const detailView = document.getElementById('detail-view');
    const backBtn = document.getElementById('back-btn');
    const viewTitle = document.getElementById('view-title');
    const viewSubtitle = document.getElementById('view-subtitle');

    // Update Sidebar active states
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    if (view === 'overview') {
        overviewView.className = 'active-view';
        detailView.className = 'hidden-view';
        backBtn.classList.add('hidden');
        viewTitle.textContent = "Telemetry Overview";
        viewSubtitle.textContent = "Live network status and cluster aggregation";

        const homeNav = document.querySelector('.nav-item[data-view="overview"]');
        if (homeNav) homeNav.classList.add('active');

        // Extract cluster set strictly from current parsed nodes
        const currentClusters = new Set();
        state.nodes.forEach(records => {
            if (records.length > 0 && records[records.length - 1].cid) {
                currentClusters.add(records[records.length - 1].cid);
            }
        });
        renderOverview(currentClusters);

        // Leaflet needs to know the container changed visibility to redraw tiles properly
        if (state.map) {
            setTimeout(() => {
                state.map.invalidateSize();
            }, 100);
        }

    } else if (view === 'detail') {
        overviewView.className = 'hidden-view';
        detailView.className = 'active-view';
        backBtn.classList.remove('hidden');

        let clusterName = `Cluster ${clusterId}`;
        if (state.config[clusterId] && state.config[clusterId].name) {
            clusterName = `${state.config[clusterId].name} Cluster`;
        }
        viewTitle.textContent = clusterName;
        viewSubtitle.textContent = `Cluster Head ID: ${clusterId}`;

        const clusterNav = document.querySelector(`.nav-item[data-cluster="${clusterId}"]`);
        if (clusterNav) clusterNav.classList.add('active');

        renderClusterDetail(clusterId);
    }
    else if (view === 'ferried') {
        overviewView.className = 'hidden-view';
        detailView.className = 'hidden-view';
        backBtn.classList.add('hidden');
        viewTitle.textContent = 'Ferried Data';
        viewSubtitle.textContent = 'Data relayed from the drone companion';
        const ferriedView = document.getElementById('ferried-view');
        if (ferriedView) ferriedView.className = 'active-view';
        if (state.map) setTimeout(() => state.map.invalidateSize(), 100);
    }
}

function renderSidebar(clustersSeen) {
    const list = document.getElementById('cluster-links');
    list.innerHTML = ''; // Clear current

    // Sort clusters for consistent ordering
    const sortedClusters = Array.from(clustersSeen).sort();

    sortedClusters.forEach(cid => {
        let clusterName = `Cluster ${cid}`;
        if (state.config[cid] && state.config[cid].name) {
            clusterName = `${state.config[cid].name} Cluster`;
        }

        const li = document.createElement('li');
        li.className = `nav-item ${state.activeCluster == cid ? 'active' : ''}`;
        li.dataset.cluster = cid;
        li.innerHTML = `
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <circle cx="12" cy="12" r="3"></circle>
            </svg>
            ${clusterName}
        `;
        li.addEventListener('click', () => switchView('detail', cid));
        list.appendChild(li);
    });
}

function renderOverview(clustersSeen) {
    const container = document.getElementById('cluster-cards-container');
    container.innerHTML = ''; // Clear current

    const sortedClusters = Array.from(clustersSeen).sort();

    sortedClusters.forEach(cid => {
        let clusterName = `Cluster ${cid}`;
        if (state.config[cid] && state.config[cid].name) {
            clusterName = `${state.config[cid].name} Cluster`;
        }

        // Calculate basic stats for this cluster
        let nodeCount = 0;
        let latestAvgTemp = 0;

        state.nodes.forEach((records) => {
            if (records.length > 0 && records[records.length - 1].cid == cid) {
                nodeCount++;
                latestAvgTemp += records[records.length - 1].t;
            }
        });

        if (nodeCount > 0) latestAvgTemp = (latestAvgTemp / nodeCount).toFixed(1);

        const card = document.createElement('div');
        card.className = 'cluster-summary-card glass-panel';
        card.innerHTML = `
            <h3>${clusterName}</h3>
            <p>ID: ${cid}</p>
            <div class="stats">
                <div><strong>${nodeCount}</strong> Nodes</div>
                <div>Avg Temp: <strong>${latestAvgTemp}°C</strong></div>
            </div>
        `;
        card.addEventListener('click', () => switchView('detail', cid));
        container.appendChild(card);
    });
}

function renderClusterDetail(clusterId) {
    // We clear the nodes container and rebuild it only for the active cluster
    const container = document.getElementById('nodes-container');
    container.innerHTML = '';

    // Track which nodes belong to this cluster
    state.nodes.forEach((records, nodeId) => {
        if (records.length === 0) return;
        const latestRecord = records[records.length - 1];

        if (latestRecord.cid == clusterId) {
            // Force recreation of the card in the new container
            createNodeCardDom(nodeId, records, container, clusterId);
            updateNodeCardValues(nodeId, records);
            initChart(nodeId);
            updateChart(nodeId, records);
        }
    });
}

function updateMap(clustersSeen) {
    if (!state.map) return;
    console.debug('Dashboard: updateMap called. clustersSeen=', clustersSeen);
    console.debug('Dashboard: config keys=', Object.keys(state.config || {}));

    // Clear old layers
    state.mapLayerGroups.forEach(layer => state.map.removeLayer(layer));
    state.mapLayerGroups.clear();

    const bounds = [];

    // If there is no telemetry yet (no clustersSeen), show configured nodes from state.config
    if (!clustersSeen || clustersSeen.size === 0) {
        const configuredGroup = L.layerGroup();
        Object.keys(state.config).forEach(nodeId => {
            const cfg = state.config[nodeId];
            if (!cfg || cfg.lat == null || cfg.lng == null) return;
            const latLng = [cfg.lat, cfg.lng];
            bounds.push(latLng);

            const isCH = cfg.is_ch === true;
            const markerColor = isCH ? '#3b82f6' : '#10b981';

            const marker = L.circleMarker(latLng, {
                radius: isCH ? 8 : 6,
                fillColor: markerColor,
                color: '#ffffff',
                weight: 2,
                opacity: 1,
                fillOpacity: 0.8
            });

            const tooltipHtml = `<strong>${cfg.name || nodeId}</strong><br>ID: ${nodeId}`;
            marker.bindTooltip(tooltipHtml, { direction: 'top' });

            const popupHtml = `
                <div style="text-align:left; color:#171a21;">
                    <strong>${cfg.name || nodeId}</strong><br>
                    Node ID: ${nodeId}<br>
                    Lat: ${cfg.lat}, Lon: ${cfg.lng}<br>
                    ${isCH ? '<span style="color:#3b82f6; font-weight:bold;">Cluster Head</span>' : 'Member'}
                    <br><button id="toggle-ch-${nodeId}" class="btn-small">Toggle CH</button>
                </div>
            `;
            marker.bindPopup(popupHtml);

            marker.on('popupopen', () => {
                const btn = document.getElementById(`toggle-ch-${nodeId}`);
                if (btn) {
                    btn.addEventListener('click', () => {
                                if (!state.config[nodeId]) state.config[nodeId] = {};
                                state.config[nodeId].is_ch = !state.config[nodeId].is_ch;
                                const nowCH = state.config[nodeId].is_ch === true;
                                marker.setStyle({ fillColor: nowCH ? '#3b82f6' : '#10b981', radius: nowCH ? 8 : 6 });
                                // Do NOT auto-persist here; persistence occurs when user clicks Save CHs
                                marker.closePopup();
                    });
                }
            });

            // If in waypoint mode, clicking a cluster-configured marker (only CH allowed) should select it
            marker.on('click', () => {
                // Priority: waypoint selection, then CH edit mode, then default
                if (state.waypointMode) {
                    const cfg2 = state.config[nodeId] || {};
                    if (cfg2.is_ch === true) {
                        handleWaypointSelect(nodeId, cfg2.lat, cfg2.lng);
                    } else {
                        console.log('Waypoint selection ignored; node is not a Cluster Head');
                    }
                    return;
                }
                if (state.chEditMode) {
                    if (!state.config[nodeId]) state.config[nodeId] = { lat: cfg.lat, lng: cfg.lng, name: cfg.name };
                    state.config[nodeId].is_ch = !state.config[nodeId].is_ch;
                    const nowCH = state.config[nodeId].is_ch === true;
                    marker.setStyle({ fillColor: nowCH ? '#3b82f6' : '#10b981', radius: nowCH ? 8 : 6 });
                    return;
                }
                // default: open popup
                marker.openPopup();
            });

            marker.addTo(configuredGroup);
            state.nodeMarkers.set(nodeId, marker);
        });

        if (configuredGroup.getLayers().length > 0) {
            configuredGroup.addTo(state.map);
            state.mapLayerGroups.set('__configured__', configuredGroup);
        }
    }

    // For each active cluster
    clustersSeen.forEach(cid => {
        const clusterGroup = L.layerGroup();
        const nodesInCluster = [];
        const unmatched = [];

        // Find nodes in this cluster
        state.nodes.forEach((records, nodeId) => {
            if (records.length === 0) return;
            // Skip any node IDs that are actually present as active drones to avoid
            // accidentally treating drone telemetry as node telemetry (this can
            // create stray polylines between drone and nodes).
            if (state.drones && state.drones.has && state.drones.has(nodeId)) return;

            if (records[records.length - 1].cid == cid) {
                // Try to find config for this nodeId (handles node_001 vs 1001 mismatches)
                const cfg = findConfigById(nodeId);
                if (cfg && cfg.lat != null && cfg.lng != null) {
                    nodesInCluster.push({
                        id: nodeId,
                        lat: cfg.lat,
                        lng: cfg.lng,
                        name: cfg.name,
                        isCH: nodeId == cid
                    });
                } else {
                    unmatched.push(nodeId);
                }
            }
        });

        if (unmatched.length > 0) {
            console.debug('Dashboard: unmatched telemetry node IDs for cluster', cid, unmatched);
        }

        if (nodesInCluster.length === 0) return;

        // Add markers
        nodesInCluster.forEach(node => {
            const latLng = [node.lat, node.lng];
            bounds.push(latLng);
            // Determine CH from config if available
            const cfg = state.config[node.id] || {};
            const isCH = cfg.is_ch === true || node.isCH === true;
            const markerColor = isCH ? '#3b82f6' : '#10b981'; // Blue for CH, Green for Member

            // Custom circle marker
            const marker = L.circleMarker(latLng, {
                radius: isCH ? 8 : 6,
                fillColor: markerColor,
                color: '#ffffff',
                weight: 2,
                opacity: 1,
                fillOpacity: 0.8
            });

            // Tooltip shown on hover with metadata
            const tooltipHtml = `<strong>${node.name}</strong><br>ID: ${node.id}`;
            marker.bindTooltip(tooltipHtml, { direction: 'top' });

            // Popup with richer info and CH toggle
            const popupHtml = `
                <div style="text-align:left; color:#171a21;">
                    <strong>${node.name}</strong><br>
                    Node ID: ${node.id}<br>
                    Lat: ${node.lat}, Lon: ${node.lng}<br>
                    ${isCH ? '<span style="color:#3b82f6; font-weight:bold;">Cluster Head</span>' : 'Member'}
                    <br><button id="toggle-ch-${node.id}" class="btn-small">Toggle CH</button>
                </div>
            `;
            marker.bindPopup(popupHtml);

            // When popup opens, wire the toggle button (uses event delegation timing)
            marker.on('popupopen', () => {
                const btn = document.getElementById(`toggle-ch-${node.id}`);
                if (btn) {
                    btn.addEventListener('click', () => {
                        // Toggle is_ch in state.config (local only). Persistence on Save CHs.
                        if (!state.config[node.id]) state.config[node.id] = { lat: node.lat, lng: node.lng, name: node.name };
                        state.config[node.id].is_ch = !state.config[node.id].is_ch;
                        // Update marker style immediately
                        const nowCH = state.config[node.id].is_ch === true;
                        marker.setStyle({ fillColor: nowCH ? '#3b82f6' : '#10b981', radius: nowCH ? 8 : 6 });
                        marker.closePopup();
                    });
                }
            });

            // Double-click navigates to detail view
            marker.on('dblclick', () => {
                switchView('detail', cid);
            });

            // Single-click: waypoint selection (if waypointMode), CH edit toggle (if chEditMode), otherwise open popup
            marker.on('click', () => {
                if (state.waypointMode) {
                    const cfg2 = state.config[node.id] || {};
                    const allowed = (cfg2.is_ch === true) || (node.isCH === true);
                    if (allowed) {
                        handleWaypointSelect(node.id, node.lat, node.lng);
                    } else {
                        console.log('Waypoint selection ignored; node is not a Cluster Head');
                    }
                    return;
                }
                if (state.chEditMode) {
                    if (!state.config[node.id]) state.config[node.id] = { lat: node.lat, lng: node.lng, name: node.name };
                    state.config[node.id].is_ch = !state.config[node.id].is_ch;
                    const nowCH = state.config[node.id].is_ch === true;
                    marker.setStyle({ fillColor: nowCH ? '#3b82f6' : '#10b981', radius: nowCH ? 8 : 6 });
                    return;
                }
                marker.openPopup();
            });

            marker.addTo(clusterGroup);
            // Track marker by node id so we can update style later
            state.nodeMarkers.set(node.id, marker);
        });

        // Add a polyline connecting the cluster head to its members
        const chNode = nodesInCluster.find(n => n.isCH);
        if (chNode && nodesInCluster.length > 1) {
            nodesInCluster.forEach(node => {
                if (!node.isCH) {
                    L.polyline([[chNode.lat, chNode.lng], [node.lat, node.lng]], {
                        color: 'rgba(59, 130, 246, 0.5)',
                        weight: 2,
                        dashArray: '5, 5'
                    }).addTo(clusterGroup);
                }
            });
        }

        clusterGroup.addTo(state.map);
        state.mapLayerGroups.set(cid, clusterGroup);
    });

    // Auto fit bounds only once on first meaningful data load so user panning is not interrupted
    if (bounds.length > 0 && !state.mapHasFitBounds) {
        state.map.fitBounds(L.latLngBounds(bounds).pad(0.1));
        state.mapHasFitBounds = true;
    }
}

// Drone tracking: maintain moving markers and track polylines
function updateDrones(droneRecords) {
    if (!state.map || !state.droneLayerGroup) return;

    // Group records by drone id
    const byId = new Map();
    droneRecords.forEach(r => {
        const id = r.id || r.drone_id || 'drone';
        if (!byId.has(id)) byId.set(id, []);
        byId.get(id).push(r);
    });

    byId.forEach((records, id) => {
        // Ensure we have an entry for this drone
        if (!state.drones.has(id)) {
            state.drones.set(id, { positions: [], marker: null, polyline: null });
        }

        const info = state.drones.get(id);

        // Append new positions (ensure lat/lon present)
        records.forEach(r => {
            if (r.lat == null || r.lon == null) return;
            info.positions.push({ lat: r.lat, lng: r.lon, ts: r.ts || Date.now() / 1000 });
        });

        // Limit track length
        const MAX_POINTS = 200;
        if (info.positions.length > MAX_POINTS) info.positions = info.positions.slice(-MAX_POINTS);

        // Create marker if missing (use rotating divIcon)
        if (!info.marker) {
            const last = info.positions[info.positions.length - 1];
            if (!last) return;
            const heading = (info.lastTelemetry && info.lastTelemetry.heading) ? info.lastTelemetry.heading : 0;
            const svg = `<svg width="28" height="28" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2 L15 12 L12 9 L9 12 Z" fill="#ffffff"/></svg>`;
            const html = `<div class="drone-icon" style="transform:rotate(${heading}deg);">${svg}</div>`;
            const icon = L.divIcon({ className: 'drone-div-icon', html: html, iconSize: [28,28], iconAnchor: [14,14] });
            const marker = L.marker([last.lat, last.lng], { icon: icon }).bindPopup(`<strong>${id}</strong>`);
            marker.addTo(state.droneLayerGroup);
            info.marker = marker;
        }

        // Merge latest telemetry fields into info.lastTelemetry
        if (!info.lastTelemetry) info.lastTelemetry = {};
        records.forEach(r => {
            // merge shallow fields; prefer newer records
            Object.keys(r).forEach(k => { info.lastTelemetry[k] = r[k]; });
        });

        // Update active telemetry panel when new telemetry arrives
        setActiveDroneTelemetry(id, info.lastTelemetry);

        // Update marker position to last point
        const lastPos = info.positions[info.positions.length - 1];
        if (lastPos) {
            info.marker.setLatLng([lastPos.lat, lastPos.lng]);
            // Build popup with richer telemetry
            const lt = info.lastTelemetry || {};
            const tsStr = new Date((lt.ts || lastPos.ts || Date.now()/1000) * 1000).toLocaleTimeString();
            const heading = lt.heading != null ? `Heading: ${lt.heading}°` : '';
            const alt = lt.alt != null ? `Alt: ${lt.alt} m` : '';
            const gs = lt.groundspeed != null ? `GS: ${lt.groundspeed} m/s` : '';
            const roll = lt.roll != null ? `Roll: ${lt.roll}°` : '';
            const pitch = lt.pitch != null ? `Pitch: ${lt.pitch}°` : '';
            const yaw = lt.yaw != null ? `Yaw: ${lt.yaw}°` : '';
            const mode = (lt.custom_mode != null || lt.base_mode != null || lt.system_status != null) ? `Mode: bm=${lt.base_mode} cm=${lt.custom_mode} st=${lt.system_status}` : '';

            const popupHtml = `<div style="text-align:left;"><strong>${id}</strong><br>${tsStr}<br>${heading}<br>${alt}<br>${gs}<br>${roll} ${pitch} ${yaw}<br>${mode}</div>`;
            info.marker.setPopupContent(popupHtml);
            // Update icon rotation if using divIcon
            try {
                const el = info.marker.getElement();
                if (el) {
                    const ico = el.querySelector('.drone-icon');
                    if (ico && lt.heading != null) ico.style.transform = `rotate(${lt.heading}deg)`;
                }
            } catch (e) {
                // ignore
            }
        }

        // Update polyline
        const latlngs = info.positions.map(p => [p.lat, p.lng]);
        if (!info.polyline) {
            info.polyline = L.polyline(latlngs, { color: '#ff3b3b', weight: 2, opacity: 0.8 }).addTo(state.droneLayerGroup);
        } else {
            info.polyline.setLatLngs(latlngs);
        }

        // Draw a small heading line / arrow from the drone showing direction if heading is present
        const lt = info.lastTelemetry || {};
        if (lt.heading != null && lastPos) {
            // Convert heading degrees to endpoint ~20 meters ahead
            const bearing = (lt.heading * Math.PI) / 180.0;
            const meters = 20;
            const lat = lastPos.lat;
            const lon = lastPos.lng;
            // Approx meters to degrees
            const deltaLat = (meters * Math.cos(bearing)) / 111320.0;
            const deltaLon = (meters * Math.sin(bearing)) / (111320.0 * Math.cos(lat * Math.PI / 180.0));
            const endLat = lat + deltaLat;
            const endLon = lon + deltaLon;

            const headingLatLngs = [[lat, lon], [endLat, endLon]];
            if (!info.headingLine) {
                info.headingLine = L.polyline(headingLatLngs, { color: '#f59e0b', weight: 2, opacity: 0.9 }).addTo(state.droneLayerGroup);
            } else {
                info.headingLine.setLatLngs(headingLatLngs);
            }
        }

        // Optionally auto-pan / fit bounds to include drones when map has no bounds yet
        if (!state.mapHasFitBounds && latlngs.length > 0) {
            try {
                state.map.fitBounds(latlngs);
                state.mapHasFitBounds = true;
            } catch (e) {
                // ignore
            }
        }
    });
}

// Telemetry side panel update
function setActiveDroneTelemetry(id, telemetry) {
    try {
        document.getElementById('telemetry-id').textContent = id || '-';
        document.getElementById('telemetry-time').textContent = telemetry.ts ? new Date(telemetry.ts*1000).toLocaleTimeString() : '-';
        document.getElementById('telemetry-loc').textContent = (telemetry.lat && telemetry.lon) ? `${telemetry.lat.toFixed(6)}, ${telemetry.lon.toFixed(6)}` : '-';
        document.getElementById('telemetry-alt').textContent = telemetry.alt != null ? `${telemetry.alt} m` : '-';
        document.getElementById('telemetry-heading').textContent = telemetry.heading != null ? `${telemetry.heading}°` : '-';
        document.getElementById('telemetry-gs').textContent = telemetry.groundspeed != null ? `${telemetry.groundspeed} m/s` : '-';
        document.getElementById('telemetry-rpy').textContent = (telemetry.roll!=null||telemetry.pitch!=null||telemetry.yaw!=null) ? `${telemetry.roll||'-'} / ${telemetry.pitch||'-'} / ${telemetry.yaw||'-'}` : '-';
        // Map custom_mode via human-readable map
        const modeStr = interpretMode(telemetry);
        document.getElementById('telemetry-mode').textContent = modeStr;
        document.getElementById('telemetry-batt').textContent = telemetry.battery != null ? `${telemetry.battery} V` : '-';
    } catch (e) {
        // ignore DOM errors when panel missing
    }
}

// Interpret ArduCopter custom_mode/base_mode/system_status into human string
function interpretMode(telemetry) {
    const custom = telemetry.custom_mode;
    const base = telemetry.base_mode;
    const sys = telemetry.system_status;

    const modeMap = {
        0: 'STABILIZE',
        1: 'ACRO',
        2: 'ALT_HOLD',
        3: 'AUTO',
        4: 'GUIDED',
        5: 'LOITER',
        6: 'RTL',
        7: 'CIRCLE',
        9: 'LAND',
        11: 'DRIFT',
        13: 'SPORT',
        16: 'POSHOLD',
        17: 'BRAKE',
        18: 'THROW',
        20: 'GUIDED_NOGPS',
        21: 'SMART_RTL'
    };

    if (custom != null && modeMap[custom]) return `${modeMap[custom]} (${custom})`;
    if (base != null) return `base:${base}`;
    if (sys != null) return `sys:${sys}`;
    return '-';
}

// UI Updates - Node Cards
function createNodeCardDom(nodeId, records, container, cid) {
    const latestRecord = records[records.length - 1]; // Get most recent reading
    const isCH = (nodeId == cid);

    let cardId = `node-card-${nodeId}`;

    const template = document.getElementById('node-card-template');
    const clone = template.content.cloneNode(true);

    const cardEl = clone.querySelector('.node-card');
    cardEl.id = cardId;

    if (isCH) {
        cardEl.style.border = '1px solid var(--accent-primary)';
        cardEl.style.boxShadow = '0 0 15px rgba(59, 130, 246, 0.15)';
        cardEl.querySelector('.node-badge').textContent = 'Cluster Head';
        cardEl.querySelector('.node-badge').style.background = 'rgba(59, 130, 246, 0.2)';
    }

    // Try to inject GPS info from config map
    let locationName = `Node ${nodeId}`;
    let gpsCoords = "";

    const gpsData = findConfigById(nodeId) || state.config[nodeId];
    if (gpsData) {
        locationName = `${gpsData.name || nodeId} (Node ${nodeId})`;
        gpsCoords = (gpsData.lat != null && gpsData.lng != null) ? `${gpsData.lat}, ${gpsData.lng}` : '';
    }

    // Set static identity info
    cardEl.querySelector('.node-id').textContent = locationName;
    cardEl.querySelector('.node-mac').textContent = gpsCoords ? `GPS: ${gpsCoords}` : `MAC: ${latestRecord.mac || 'Unknown'}`;

    container.appendChild(clone);
}

function updateNodeCardValues(nodeId, records) {
    const latestRecord = records[records.length - 1];
    const cardEl = document.getElementById(`node-card-${nodeId}`);

    if (!cardEl) return;

    // Update latest metrics in the header
    cardEl.querySelector('.temp-val').innerHTML = `${latestRecord.t.toFixed(1)}<span class="unit">°C</span>`;
    cardEl.querySelector('.hum-val').innerHTML = `${latestRecord.h.toFixed(1)}<span class="unit">%</span>`;
    cardEl.querySelector('.aqi-val').textContent = latestRecord.aqi;
    cardEl.querySelector('.eco2-val').innerHTML = `${latestRecord.eco2}<span class="unit">ppm</span>`;
}

function updateTimestamp() {
    state.lastUpdated = new Date();
    document.getElementById('last-updated').textContent = `Last Updated: ${state.lastUpdated.toLocaleTimeString()}`;
}

// Chart.js Integration
function initChart(nodeId) {
    const cardEl = document.getElementById(`node-card-${nodeId}`);
    const ctx = cardEl.querySelector('.node-chart').getContext('2d');

    Chart.defaults.color = '#a0aabf';
    Chart.defaults.font.family = "'Inter', sans-serif";

    const chart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [
                {
                    label: 'Temperature (°C)',
                    borderColor: CHART_COLORS.temp,
                    backgroundColor: CHART_COLORS.temp + '15', // Transparent fill
                    data: [],
                    yAxisID: 'y',
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 2,
                    pointHoverRadius: 6,
                    fill: true
                },
                {
                    label: 'Humidity (%)',
                    borderColor: CHART_COLORS.hum,
                    backgroundColor: CHART_COLORS.hum + '15', // Transparent fill
                    data: [],
                    yAxisID: 'y1',
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 2,
                    pointHoverRadius: 6,
                    fill: true
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 400, // Smooth transition for live updates
                easing: 'linear'
            },
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        usePointStyle: true,
                        boxWidth: 8
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(23, 26, 33, 0.95)',
                    titleColor: '#fff',
                    bodyColor: '#e2e8f0',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    padding: 12,
                    displayColors: true,
                    intersect: false,
                    mode: 'index'
                }
            },
            scales: {
                x: {
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        maxRotation: 0,
                        autoSkip: true,
                        maxTicksLimit: 8
                    }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Temp (°C)',
                        color: CHART_COLORS.temp,
                        font: { size: 10, weight: '600' }
                    },
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        borderDash: [5, 5]
                    }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    title: {
                        display: true,
                        text: 'Humidity (%)',
                        color: CHART_COLORS.hum,
                        font: { size: 10, weight: '600' }
                    },
                    grid: {
                        drawOnChartArea: false
                    }
                }
            }
        }
    });

    state.charts.set(nodeId, chart);
}

function updateChart(nodeId, records) {
    const chart = state.charts.get(nodeId);
    if (!chart) return;

    // Generate formatted time labels directly
    chart.data.labels = records.map(r => new Date(r.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));

    // Inject raw data into datasets
    chart.data.datasets[0].data = records.map(r => r.t);
    chart.data.datasets[1].data = records.map(r => r.h);

    chart.update('none'); // Update without full animation for performance
}
