import copy
import weakref
from queue import Empty, Queue

import carla
import numpy as np
from gymnasium import spaces

from carla_gym.core.obs_manager.obs_manager import ObsManagerBase


class ObsManager(ObsManagerBase):
    """
    Template configs:
    obs_configs = {
        "module": "camera.rgb",
        "location": [-5.5, 0, 2.8],
        "rotation": [0, -15, 0],
        "frame_stack": 1,
        "width": 1920,
        "height": 1080
    }
    frame_stack: [Image(t-2), Image(t-1), Image(t)]
    """

    def __init__(self, obs_configs):
        self._sensor_type = "camera.rgb"

        self._height = obs_configs["height"]
        self._width = obs_configs["width"]
        self._fov = obs_configs["fov"]
        self._channels = 3
        location = carla.Location(
            x=float(obs_configs["location"][0]),
            y=float(obs_configs["location"][1]),
            z=float(obs_configs["location"][2]),
        )
        rotation = carla.Rotation(
            roll=float(obs_configs["rotation"][0]),
            pitch=float(obs_configs["rotation"][1]),
            yaw=float(obs_configs["rotation"][2]),
        )

        self._camera_transform = carla.Transform(location, rotation)

        self._bev_height = obs_configs["bev_height"]
        self._bev_width = obs_configs["bev_width"]
        self._bev_channels = 3
        self._bev_fov = obs_configs["bev_fov"]

        location = carla.Location(
            x=float(obs_configs["bev_location"][0]),
            y=float(obs_configs["bev_location"][1]),
            z=float(obs_configs["bev_location"][2]),
        )
        rotation = carla.Rotation(
            roll=float(obs_configs["bev_rotation"][0]),
            pitch=float(obs_configs["bev_rotation"][1]),
            yaw=float(obs_configs["bev_rotation"][2]),
        )
        self._bev_transform = carla.Transform(location, rotation)

        self._sensor = None
        self._bev_sensor = None
        self._imu_sensor = None
        self._queue_timeout = 10.0
        self._image_queue = None
        self._bev_image_queue = None
        self._compass_queue = None

        super(ObsManager, self).__init__()

    def _define_obs_space(self):
        self.obs_space = spaces.Dict(
            {
                "frame": spaces.Discrete(2**32 - 1),
                "data": spaces.Box(
                    low=0,
                    high=255,
                    shape=(self._height, self._width, self._channels),
                    dtype=np.uint8,
                ),
                "bev_data": spaces.Box(
                    low=0,
                    high=255,
                    shape=(self._bev_height, self._bev_width, self._bev_channels),
                    dtype=np.uint8,
                ),
                "compass": spaces.Box(low=-np.inf, high=np.inf, shape=(1,), dtype=np.float32),
            }
        )

    def attach_ego_vehicle(self, parent_actor):
        init_obs = np.zeros([self._height, self._width, self._channels], dtype=np.uint8)
        self._image_queue = Queue()
        self._bev_image_queue = Queue()
        self._compass_queue = Queue()

        self._world = parent_actor.vehicle.get_world()
        weak_self = weakref.ref(self)
        # RGB Camera
        bp = self._world.get_blueprint_library().find("sensor." + self._sensor_type)
        bp.set_attribute("image_size_x", str(self._width))
        bp.set_attribute("image_size_y", str(self._height))
        bp.set_attribute("fov", str(self._fov))
        # set in leaderboard
        bp.set_attribute("lens_circle_multiplier", str(3.0))
        bp.set_attribute("lens_circle_falloff", str(3.0))
        bp.set_attribute("chromatic_aberration_intensity", str(0.5))
        bp.set_attribute("chromatic_aberration_offset", str(0))

        self._sensor = self._world.spawn_actor(
            bp, self._camera_transform, attach_to=parent_actor.vehicle
        )
        self._sensor.listen(lambda image: self._parse_image(weak_self, image))

        # BEV Camera
        bp = self._world.get_blueprint_library().find("sensor." + self._sensor_type)
        bp.set_attribute("image_size_x", str(self._bev_width))
        bp.set_attribute("image_size_y", str(self._bev_height))
        bp.set_attribute("fov", str(self._bev_fov))
        self._bev_sensor = self._world.spawn_actor(
            bp, self._bev_transform, attach_to=parent_actor.vehicle
        )
        self._bev_sensor.listen(lambda image: self._bev_parse_image(weak_self, image))

        # IMU sensor
        bp = self._world.get_blueprint_library().find("sensor.other.imu")
        bp.set_attribute("sensor_tick", "0.05")
        self._imu_sensor = self._world.spawn_actor(
            bp, carla.Transform(), attach_to=parent_actor.vehicle
        )
        self._imu_sensor.listen(lambda imu: self._parse_imu(weak_self, imu))

    def get_observation(self):
        snap_shot = self._world.get_snapshot()
        assert self._image_queue.qsize() <= 1

        try:
            frame, data = self._image_queue.get(True, self._queue_timeout)
            bev_frame, bev_data = self._bev_image_queue.get(True, self._queue_timeout)
            compass_frame, compass_data = self._compass_queue.get(True, self._queue_timeout)
            assert snap_shot.frame == frame
            assert snap_shot.frame == bev_frame
            assert snap_shot.frame == compass_frame
        except Empty:
            raise Exception("RGB sensor took too long!")

        obs = {
            "frame": frame,
            "data": data,
            "bev_data": bev_data,
            "compass": compass_data,
        }

        return obs

    def clean(self):
        if self._sensor and self._sensor.is_alive:
            self._sensor.stop()
            self._sensor.destroy()
        if self._bev_sensor and self._bev_sensor.is_alive:
            self._bev_sensor.stop()
            self._bev_sensor.destroy()
        if self._imu_sensor and self._imu_sensor.is_alive:
            self._imu_sensor.stop()
            self._imu_sensor.destroy()

        self._sensor = None
        self._bev_sensor = None
        self._imu_sensor = None
        self._world = None

        self._image_queue = None
        self._bev_image_queue = None
        self._compass_queue = None

    @staticmethod
    def _parse_image(weak_self, carla_image):
        self = weak_self()

        np_img = np.frombuffer(carla_image.raw_data, dtype=np.dtype("uint8"))

        np_img = copy.deepcopy(np_img)

        np_img = np.reshape(np_img, (carla_image.height, carla_image.width, 4))
        np_img = np_img[:, :, :3]
        np_img = np_img[:, :, ::-1]

        # np_img = np.moveaxis(np_img, -1, 0)
        # image = cv2.resize(image, (self._res_x, self._res_y), interpolation=cv2.INTER_AREA)
        # image = np.float32
        # image = (image.astype(np.float32) - 128) / 128

        self._image_queue.put((carla_image.frame, np_img))

    @staticmethod
    def _bev_parse_image(weak_self, carla_image):
        self = weak_self()

        np_img = np.frombuffer(carla_image.raw_data, dtype=np.dtype("uint8"))

        np_img = copy.deepcopy(np_img)

        np_img = np.reshape(np_img, (carla_image.height, carla_image.width, 4))
        np_img = np_img[:, :, :3]
        np_img = np_img[:, :, ::-1]

        self._bev_image_queue.put((carla_image.frame, np_img))

    @staticmethod
    def _parse_imu(weak_self, carla_imu):
        self = weak_self()
        self._compass_queue.put((carla_imu.frame, np.array([carla_imu.compass])))
