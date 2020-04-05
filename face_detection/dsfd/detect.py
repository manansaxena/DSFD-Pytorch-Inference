import torch
import numpy as np
import typing
from .face_ssd import SSD
from .config import resnet152_model_config
from .. import torch_utils
from torch.hub import load_state_dict_from_url
from ..base import Detector
from ..build import DETECTOR_REGISTRY

model_url = "http://folk.ntnu.no//haakohu/WIDERFace_DSFD_RES152.pth"


@DETECTOR_REGISTRY.register_module
class DSFDDetector(Detector):

    def __init__(
            self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        state_dict = load_state_dict_from_url(
            model_url,
            map_location=torch_utils.get_device(),
            progress=True)
        self.net = SSD(resnet152_model_config)
        self.net.load_state_dict(state_dict)
        self.net.eval()
        self.net = self.net.to(self.device)

    @torch.no_grad()
    def _detect(self, x: torch.Tensor,) -> typing.List[np.ndarray]:
        """Batched detect
        Args:
            image (np.ndarray): shape [N, H, W, 3]
        Returns:
            boxes: list of length N with shape [num_boxes, 5] per element
        """
        # Expects BGR
        x = x[:, [2, 1, 0], :, :]
        boxes = self.net(
            x, self.confidence_threshold, self.nms_iou_threshold
        )
        return boxes

    def multi_scale_test(
            self,
            image: np.ndarray,
            max_im_shrink: float):
        # shrink detecting and shrink only detect big face
        st = 0.5 if max_im_shrink >= 0.75 else 0.5 * max_im_shrink
        det_s = self.detect(
            image, confidence_threshold, nms_iou_threshold, shrink=st)
        if max_im_shrink > 0.75:
            det2 = self.detect(
                image, shrink=0.75)
            det_s = np.row_stack((det_s, det2))
        index = np.where(np.maximum(det_s[:, 2] - det_s[:, 0] + 1, det_s[:, 3] - det_s[:, 1] + 1) > 30)[0]
        det_s = det_s[index, :]
        # enlarge one times
        bt = min(2, max_im_shrink) if max_im_shrink > 1 else (st + max_im_shrink) / 2
        det_b = self.detect(
            image, shrink=bt)

        # enlarge small iamge x times for small face
        if max_im_shrink > 1.5:
            det3 = self.detect(
                image, shrink=1.5)
            det_b = np.row_stack((det_b, det3))
        if max_im_shrink > 2:
            bt *= 2
            while bt < max_im_shrink:  # and bt <= 2:
                det4 = self.detect(
                    image, shrink=bt)
                det_b = np.row_stack((det_b, det4))
                bt *= 2
            det5 = self.detect(
                image,
                shrink=max_im_shrink)
            det_b = np.row_stack((det_b, det5))

        # enlarge only detect small face
        if bt > 1:
            index = np.where(np.minimum(det_b[:, 2] - det_b[:, 0] + 1, det_b[:, 3] - det_b[:, 1] + 1) < 100)[0]
            det_b = det_b[index, :]
        else:
            index = np.where(np.maximum(det_b[:, 2] - det_b[:, 0] + 1, det_b[:, 3] - det_b[:, 1] + 1) > 30)[0]
            det_b = det_b[index, :]

        return det_s, det_b

    def multi_scale_test_pyramid(
            self,
            image: np.ndarray,
            confidence_threshold: float,
            nms_iou_threshold: float,
            max_shrink: float):
        # shrink detecting and shrink only detect big face
        det_b = self.detect(
            image, shrink=0.25)
        index = np.where(
            np.maximum(det_b[:, 2] - det_b[:, 0] + 1, det_b[:, 3] - det_b[:, 1] + 1)
            > 30)[0]
        det_b = det_b[index, :]

        st = [1.25, 1.75, 2.25]
        for i in range(len(st)):
            if (st[i] <= max_shrink):
                det_temp = self.detect(
                    image, shrink=st[i])
                # enlarge only detect small face
                if st[i] > 1:
                    index = np.where(
                        np.minimum(det_temp[:, 2] - det_temp[:, 0] + 1,
                                   det_temp[:, 3] - det_temp[:, 1] + 1) < 100)[0]
                    det_temp = det_temp[index, :]
                else:
                    index = np.where(
                        np.maximum(det_temp[:, 2] - det_temp[:, 0] + 1,
                                   det_temp[:, 3] - det_temp[:, 1] + 1) > 30)[0]
                    det_temp = det_temp[index, :]
                det_b = np.row_stack((det_b, det_temp))
        return det_b

    def flip_test(
            self,
            image: np.ndarray,
            confidence_threshold: float,
            nms_iou_threshold: float,
            shrink: float):
        image_f = cv2.flip(image, 1)
        det_f = self.detect(
            image_f, shrink=shrink)

        det_t = np.zeros(det_f.shape)
        det_t[:, 0] = image.shape[1] - det_f[:, 2]
        det_t[:, 1] = det_f[:, 1]
        det_t[:, 2] = image.shape[1] - det_f[:, 0]
        det_t[:, 3] = det_f[:, 3]
        det_t[:, 4] = det_f[:, 4]
        return det_t


def bbox_vote(det):
    order = det[:, 4].ravel().argsort()[::-1]
    det = det[order, :]
    if det.shape[0] == 0:
        return det[0:750, :]
    dets = None
    while det.shape[0] > 0:
        # IOU
        area = (det[:, 2] - det[:, 0] + 1) * (det[:, 3] - det[:, 1] + 1)
        xx1 = np.maximum(det[0, 0], det[:, 0])
        yy1 = np.maximum(det[0, 1], det[:, 1])
        xx2 = np.minimum(det[0, 2], det[:, 2])
        yy2 = np.minimum(det[0, 3], det[:, 3])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        o = inter / (area[0] + area[:] - inter)

        # get needed merge det and delete these det
        merge_index = np.where(o >= 0.3)[0]
        det_accu = det[merge_index, :]
        det = np.delete(det, merge_index, 0)

        if merge_index.shape[0] <= 1:
            continue
        det_accu[:, 0:4] = det_accu[:, 0:4] * np.tile(det_accu[:, -1:], (1, 4))
        max_score = np.max(det_accu[:, 4])
        det_accu_sum = np.zeros((1, 5))
        det_accu_sum[:, 0:4] = np.sum(det_accu[:, 0:4], axis=0) / np.sum(det_accu[:, -1:])
        det_accu_sum[:, 4] = max_score
        if dets is None:
            dets = det_accu_sum
        else:
            dets = np.row_stack((dets, det_accu_sum))
    if dets is None:
        dets = det
    return dets[:750, :]


def get_face_detections(detector: DSFDDetector,
                        image: np.ndarray,
                        confidence_threshold: float,
                        nms_iou_threshold: float,
                        multiscale_detect: bool,
                        image_pyramid_detect: bool,
                        flip_detect: bool):
    max_im_shrink = (0x7fffffff / 200.0 / (image.shape[0] * image.shape[1])) ** 0.5 # the max size of input image for caffe
    max_im_shrink = 3 if max_im_shrink > 3 else max_im_shrink
    shrink = max_im_shrink if max_im_shrink < 1 else 1
    dets = []
    det0 = detector.detect(
        image, confidence_threshold, nms_iou_threshold, shrink)

    dets.append(det0)
    if flip_detect:
        det1 = detector.flip_test(
            image, confidence_threshold, nms_iou_threshold, shrink)
        dets.append(det1)
    if multiscale_detect:
        det2, det3 = detector.multi_scale_test(
            image, confidence_threshold, nms_iou_threshold, max_im_shrink)
        dets.extend([det2, det3])
    if image_pyramid_detect:
        det4 = detector.multi_scale_test_pyramid(
            image, confidence_threshold, nms_iou_threshold, max_im_shrink)
        dets.append(det4)
    if len(dets) > 1:
        dets = np.row_stack(dets)
        dets = bbox_vote(dets)
    else:
        dets = dets[0]
    return dets
