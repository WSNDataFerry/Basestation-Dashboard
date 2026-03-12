from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription, DeclareLaunchArgument, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource, AnyLaunchDescriptionSource
from launch_ros.actions import Node
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    ws_root = '/home/dev/ros2_ws'
    dashboard_path = os.path.join(ws_root, 'Basestation-Dashboard', 'app.py')
    telemetry_path = os.path.join(ws_root, 'Basestation-Dashboard', 'tools', 'telemetry_mavlink_to_dashboard.py')

    dashboard = TimerAction(
        period=12.0,
        actions=[
            ExecuteProcess(
                cmd=['python3', dashboard_path],
                output='screen',
                shell=False,
            )
        ]
    )

    telemetry_bridge = ExecuteProcess(
        cmd=[
            'python3',
            telemetry_path,
            '--port', '/dev/ttyUSB0',
            '--baud', '57600',
            '--id', 'drone_1'
        ],
        output='screen'
    )
    basestation = TimerAction(
        period=14.0,
        actions=[
            Node(
                package='basestation_node',
                executable='basestation_node',
                name='basestation_node',
                output='screen'
            )
        ]
    )

    return LaunchDescription([
        dashboard,
        telemetry_bridge,    # t=7s
        basestation,         # t=9s
    ])