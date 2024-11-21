#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ultralytics_ros
# Copyright (C) 2023-2024  Alpaca-zip
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import cv_bridge
import numpy as np
import roslib.packages
import rospy
from sensor_msgs.msg import Image
from ultralytics import YOLO
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose
from ultralytics_ros.msg import YoloResult


class TrackerNode:
    def __init__(self):
        # 初始设置
        yolo_model = rospy.get_param("~yolo_model", "yolov8n.pt")
        self.input_topic = rospy.get_param("~input_topic", "image_raw")
        self.result_topic = rospy.get_param("~result_topic", "yolo_result")
        self.result_image_topic = rospy.get_param("~result_image_topic", "yolo_image")
        self.conf_thres = rospy.get_param("~conf_thres", 0.25)
        self.iou_thres = rospy.get_param("~iou_thres", 0.45)
        self.max_det = rospy.get_param("~max_det", 300)
        self.classes = rospy.get_param("~classes", None)
        self.tracker = rospy.get_param("~tracker", "bytetrack.yaml")
        self.device = rospy.get_param("~device", None)
        self.result_conf = rospy.get_param("~result_conf", True)
        self.result_line_width = rospy.get_param("~result_line_width", None)
        self.result_font_size = rospy.get_param("~result_font_size", None)
        self.result_font = rospy.get_param("~result_font", "Arial.ttf")
        self.result_labels = rospy.get_param("~result_labels", True)
        self.result_boxes = rospy.get_param("~result_boxes", True)
        path = roslib.packages.get_pkg_dir("ultralytics_ros")
        self.model = YOLO(f"{path}/models/{yolo_model}")
        self.model.fuse()
        self.sub = rospy.Subscriber(
            self.input_topic,
            Image,
            self.image_callback,
            queue_size=1,
            buff_size=2**24,
        )
        self.results_pub = rospy.Publisher(self.result_topic, YoloResult, queue_size=1)
        self.result_image_pub = rospy.Publisher(
            self.result_image_topic, Image, queue_size=1
        )
        self.bridge = cv_bridge.CvBridge()
        self.use_segmentation = yolo_model.endswith("-seg.pt")
        
        # 初始化一个变量来保存第一个目标的ID
        self.first_target_id = None

    def image_callback(self, msg):
        cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")

        # 使用 YOLO 模型进行目标跟踪
        results = self.model.track(
            source=cv_image,
            conf=self.conf_thres,
            iou=self.iou_thres,
            max_det=self.max_det,
            classes=self.classes,
            tracker=self.tracker,
            device=self.device,
            verbose=False,
            retina_masks=True,
        )

        if results and len(results[0].boxes.xywh) > 0:  # 确保检测到目标
            first_result = results[0]
            
            # 如果没有记录第一个目标的 ID，则在第一次检测时记录该目标的 ID
            if self.first_target_id is None:
                self.first_target_id = first_result.boxes.cls[0].item()  # 记录第一个目标的类别ID
            
            # 只跟踪第一个目标，忽略其他目标
            for idx, cls in enumerate(first_result.boxes.cls):
                if cls.item() == self.first_target_id:
                    # 只关注第一个目标的检测结果
                    bounding_box = first_result.boxes.xywh[idx]
                    confidence_score = first_result.boxes.conf[idx]
                    
                    # 创建检测信息
                    detection = Detection2D()
                    detection.bbox.center.x = float(bounding_box[0])
                    detection.bbox.center.y = float(bounding_box[1])
                    detection.bbox.size_x = float(bounding_box[2])
                    detection.bbox.size_y = float(bounding_box[3])

                    hypothesis = ObjectHypothesisWithPose()
                    hypothesis.id = int(cls)
                    hypothesis.score = float(confidence_score)
                    detection.results.append(hypothesis)

                    # 创建结果消息
                    yolo_result_msg = YoloResult()
                    yolo_result_image_msg = Image()
                    yolo_result_msg.header = msg.header
                    yolo_result_image_msg.header = msg.header
                    yolo_result_msg.detections = [detection]  # 只发送第一个目标的检测结果

                    # 创建结果图像，只绘制第一个目标
                    yolo_result_image_msg = self.create_result_image([first_result])

                    # 如果是分割模型，处理分割掩膜
                    if self.use_segmentation:
                        yolo_result_msg.masks = self.create_segmentation_masks([first_result])

                    # 发布消息
                    self.results_pub.publish(yolo_result_msg)
                    self.result_image_pub.publish(yolo_result_image_msg)
                    break  # 找到并处理第一个目标后跳出循环

    def create_detections_array(self, results):
        detections_msg = Detection2DArray()
        bounding_box = results[0].boxes.xywh
        classes = results[0].boxes.cls
        confidence_score = results[0].boxes.conf
        for bbox, cls, conf in zip(bounding_box, classes, confidence_score):
            detection = Detection2D()
            detection.bbox.center.x = float(bbox[0])
            detection.bbox.center.y = float(bbox[1])
            detection.bbox.size_x = float(bbox[2])
            detection.bbox.size_y = float(bbox[3])
            hypothesis = ObjectHypothesisWithPose()
            hypothesis.id = int(cls)
            hypothesis.score = float(conf)
            detection.results.append(hypothesis)
            detections_msg.detections.append(detection)
        return detections_msg

    def create_result_image(self, results):
        # 只绘制第一个检测目标
        plotted_image = results[0].plot(
            conf=self.result_conf,
            line_width=self.result_line_width,
            font_size=self.result_font_size,
            font=self.result_font,
            labels=self.result_labels,
            boxes=self.result_boxes,
        )
        result_image_msg = self.bridge.cv2_to_imgmsg(plotted_image, encoding="bgr8")
        return result_image_msg


    def create_segmentation_masks(self, results):
        masks_msg = []
        for result in results:
            if hasattr(result, "masks") and result.masks is not None:
                for mask_tensor in result.masks:
                    mask_numpy = (
                        np.squeeze(mask_tensor.data.to("cpu").detach().numpy()).astype(
                            np.uint8
                        )
                        * 255
                    )
                    mask_image_msg = self.bridge.cv2_to_imgmsg(
                        mask_numpy, encoding="mono8"
                    )
                    masks_msg.append(mask_image_msg)
        return masks_msg


if __name__ == "__main__":
    rospy.init_node("tracker_node")
    node = TrackerNode()
    rospy.spin()