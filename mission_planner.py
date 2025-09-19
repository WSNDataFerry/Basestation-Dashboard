class MissionPlanner:
    def __init__(self):
        self.missions = []

    def upload_mission(self, uav, mission_type, mission_data):
        """
        Sends mission to UAV
        mission_type: 'DF', 'DR', 'HA'
        mission_data: dict containing mission parameters
        """
        print(f"Uploading {mission_type} mission to UAV")
        uav.receive_mission(mission_type, mission_data)
