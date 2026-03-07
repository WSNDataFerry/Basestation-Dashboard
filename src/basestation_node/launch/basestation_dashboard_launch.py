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

    fcu_url_arg = DeclareLaunchArgument(
        'fcu_url',
        default_value='udp://127.0.0.1:14550@14555',
        description='FCU URL for MAVROS connection'
    )

    # 1) ardupilot_gz_bringup — starts immediately
    ardupilot_gz_bringup_dir = get_package_share_directory('ardupilot_gz_bringup')
    ardupilot_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(ardupilot_gz_bringup_dir, 'launch', 'iris_runway.launch.py')
        )
    )

    mavros_dir = '/opt/ros/humble/share/mavros/launch'
    mavros_include = TimerAction(
        period=10.0,
        actions=[
            IncludeLaunchDescription(
                AnyLaunchDescriptionSource(
                    os.path.join(mavros_dir, 'px4.launch')
                ),
                launch_arguments={
                    'fcu_url': LaunchConfiguration('fcu_url')
                }.items()
            )
        ]
    )

    # 3) Flask dashboard — starts after 10 + 2 = 12 sec
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

    # 4) basestation_node — starts after 10 + 2 + 2 = 14 sec
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
        fcu_url_arg,
        ardupilot_include,   # t=0s
        mavros_include,      # t=5s
        dashboard,           # t=7s
        basestation,         # t=9s
    ])