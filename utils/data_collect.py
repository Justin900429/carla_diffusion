import argparse
import json
import os
import time

import cv2
import numpy as np
import torch
import torch.nn.functional
from hydra import compose, initialize
from PIL import Image

from env_agents import create_env


def parse_args():
    parser = argparse.ArgumentParser(description="Data Collection")
    parser.add_argument(
        "--save-path", default="data", type=str, help="Path to save the data"
    )
    parser.add_argument(
        "--config-path", default="train_rl", type=str, help="Path to the config file"
    )
    parser.add_argument(
        "--save-num", default=5000, type=int, help="The number of data to save"
    )
    return parser.parse_args()


def get_random_seed():
    t = int(time.time() * 1000.0)
    t = (
        ((t & 0xFF000000) >> 24)
        + ((t & 0x00FF0000) >> 8)
        + ((t & 0x0000FF00) << 8)
        + ((t & 0x000000FF) << 24)
    )
    return t


def way_point_to_pixel(waypoint):
    pixel_val = waypoint / 23.315 * 256
    return int(256 - pixel_val)


class Agent:
    def __init__(self, env_config_path, save_root, total_to_save, seed):
        with initialize(config_path="../config"):
            cfg = compose(config_name=env_config_path)
        self.env, self.server_manager = create_env(cfg, seed)

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.magic_number = 23.315

        self.save_root = save_root
        os.makedirs(os.path.join(self.save_root, "front"), exist_ok=True)
        os.makedirs(os.path.join(self.save_root, "bev"), exist_ok=True)
        self.target_traj_num = 15
        self.get_every_n_frame = 2
        self.tota_frame_should_pass = self.target_traj_num * self.get_every_n_frame
        self.total_to_save = total_to_save
        self.cur_save = 0

    def run(self):
        state = self.env.reset()
        cur_traj = []
        target_bev = None
        init_compass = 0.0
        big_record = []
        buffer_frame = 0

        while self.cur_save < self.total_to_save:
            input_control = {0: None}
            state, *_ = self.env.step(input_control)
            cur_pos = state["cur_waypoint"][0]
            camera = state["camera"][0]
            bev = state["bev"][0]

            if len(cur_traj) == 0:
                save_front_path = os.path.join(
                    self.save_root, "front", f"{self.cur_save:06d}.png"
                )
                Image.fromarray(camera).save(save_front_path)
                target_bev = bev
                init_compass = state["compass"][0]

            cur_traj.append(cur_pos)

            if len(cur_traj) == self.tota_frame_should_pass:
                save_bev_path = os.path.join(
                    self.save_root, "bev", f"{self.cur_save:06d}.png"
                )
                added_traj = []
                for traj in cur_traj:
                    theta = init_compass + np.pi / 2
                    R = np.array(
                        [
                            [np.cos(theta), -np.sin(theta)],
                            [np.sin(theta), np.cos(theta)],
                        ]
                    )
                    traj = np.array(
                        [traj[0] - cur_traj[0][0], traj[1] - cur_traj[0][1]]
                    )
                    traj = R.T.dot(traj).reshape(-1)
                    pixel_x = way_point_to_pixel(traj[1])
                    pixel_y = way_point_to_pixel(-traj[0])
                    target_bev = cv2.circle(
                        target_bev, (int(pixel_x), int(pixel_y)), 3, (0, 255, 0), -1
                    )
                    added_traj.append(
                        (traj[1] / self.magic_number, -traj[0] / self.magic_number)
                    )
                big_record.append(
                    {"traj": added_traj, "image": f"{self.cur_save:06d}.png"}
                )
                Image.fromarray(target_bev).save(save_bev_path)
                cur_traj.clear()
                self.cur_save += 1
                
            while buffer_frame < 50:
                state, *_ = self.env.step(input_control)
                buffer_frame += 1

        with open(os.path.join(self.save_root, "waypoint.json"), "w") as f:
            json.dump(big_record, f)
        self.server_manager.stop()
        print("Finished!")

    def __del__(self):
        self.server_manager.stop()
        print("Finished!")


if __name__ == "__main__":
    args = parse_args()
    agent = Agent(
        env_config_path=args.config_path,
        save_root=args.save_path,
        total_to_save=args.save_num,
        seed=get_random_seed(),
    )
    agent.run()