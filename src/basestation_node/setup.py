from setuptools import find_packages, setup
import os

package_name = 'basestation_node'

# Ensure the launch file path is resolved relative to this setup.py so it is
# definitely found by setuptools during the install step.
launch_file_path = os.path.join(os.path.dirname(__file__), 'launch', 'basestation_dashboard_launch.py')

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Install launch files so `ros2 launch basestation_node <file>` can find them
        ('share/' + package_name + '/launch', [launch_file_path]),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='dev',
    maintainer_email='cicmurox@gmail.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'basestation_node = basestation_node.basestation_node:main',
        ],
    },
)
