import base64
import os
import requests

class Detect:
    def __init__(self):
        # self.onnx_session = onnxruntime.InferenceSession(os.path.join(os.path.dirname(__file__), "yolo.onnx"))
        # self.model_inputs = self.onnx_session.get_inputs()
        self.session = requests.Session()

    def detect(self, slice_img_url, bg_img_url, dddd_ocr_url):
        # confidence_thres = 0.8
        # iou_thres = 0.8
        # bg_img = cv2.imdecode(np.frombuffer(img_content, np.uint8), cv2.IMREAD_ANYCOLOR)
        # img_height, img_width = bg_img.shape[:2]
        # bg_img = cv2.resize(bg_img, (320, 320))
        # image_data = np.array(bg_img) / 255.0
        # image_data = np.transpose(image_data, (2, 0, 1))
        # image_data = np.expand_dims(image_data, axis=0).astype(np.float32)
        # output = self.onnx_session.run(None, {self.model_inputs[0].name: image_data})
        # outputs = np.transpose(np.squeeze(output[0]))
        # rows = outputs.shape[0]
        # boxes, scores = [], []
        # x_factor = img_width / 320
        # y_factor = img_height / 320
        # for i in range(rows):
        #     classes_scores = outputs[i][4:]
        #     max_score = np.amax(classes_scores)
        #     if max_score >= confidence_thres:
        #         x, y, w, h = outputs[i][0], outputs[i][1], outputs[i][2], outputs[i][3]
        #         left = int((x - w / 2) * x_factor)
        #         top = int((y - h / 2) * y_factor)
        #         width = int(w * x_factor)
        #         height = int(h * y_factor)
        #         boxes.append([left, top, width, height])
        #         scores.append(max_score)
        # indices = cv2.dnn.NMSBoxes(boxes, scores, confidence_thres, iou_thres)
        # new_boxes = [boxes[i] for i in indices]
        # if len(new_boxes) != 1:
        #     new_scores = [scores[i] for i in indices]
        #     max_score_index = np.argmax(new_scores)
        #     new_boxes = [new_boxes[max_score_index]]
        # return new_boxes[0]
        
        slice_img = requests.get(slice_img_url).content
        bg_img = requests.get(bg_img_url).content
        slice_img_b64 = base64.b64encode(slice_img).decode('utf-8')
        bg_img_b64 = base64.b64encode(bg_img).decode('utf-8')
        initialize_url = dddd_ocr_url.split('/slide-match')[0] + '/initialize'
        data = {
            "ocr": "true", 
            "det": "false"
        }
        response = requests.post(initialize_url, json=data)
        result = response.json()
        # print(f"【ddddocr初始化】{result['message']}")
        slide_match_url = dddd_ocr_url
        data = {
            "target_image": slice_img_b64,
            "background_image": bg_img_b64
        }
        response = requests.post(slide_match_url, json=data)
        result = response.json()
        return result['data']['target_x']


if __name__ == "__main__":
    detect = Detect()
    slice_img_url = "https://static.geetest.com/pictures/v4_pic/slide_2024_09_02/5e2cbc60e8/slide/51765a327e404b628832c24e12c6db10.png"
    bg_img_url = "https://static.geetest.com/pictures/v4_pic/slide_2024_09_02/5e2cbc60e8/bg/51765a327e404b628832c24e12c6db10.png"
    detect.detect(slice_img_url, bg_img_url, "http://127.0.0.1:8000/slide-match")
