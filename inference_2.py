import cv2
import onnx
import torch
import argparse
import numpy as np
from models import image

import warnings
from onnx2pytorch import ConvertModel
warnings.filterwarnings("ignore", message="The given NumPy array is not writable")

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="The given NumPy array is not writable")

    # Load ONNX model
    onnx_model = onnx.load('checkpoints/efficientnet.onnx')
    pytorch_model = ConvertModel(onnx_model)
torch.manual_seed(42)


audio_args = { 'nb_samp': 64600, 'first_conv': 1024, 'in_channels': 1, 'filts': [20, [20, 20], [20, 128], [128, 128]], 'blocks': [2, 4],'nb_fc_node': 1024,'gru_node': 1024, 'nb_gru_layer': 3, 'nb_classes': 2}


def get_args(parser):
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--data_dir", type=str, default="datasets/train/fakeavceleb*")
    parser.add_argument("--LOAD_SIZE", type=int, default=256)
    parser.add_argument("--FINE_SIZE", type=int, default=224)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--gradient_accumulation_steps", type=int, default=1)
    parser.add_argument("--hidden", nargs="*", type=int, default=[])
    parser.add_argument("--hidden_sz", type=int, default=768)
    parser.add_argument("--img_embed_pool_type", type=str, default="avg", choices=["max", "avg"])
    parser.add_argument("--img_hidden_sz", type=int, default=1024)
    parser.add_argument("--include_bn", type=int, default=True)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--lr_factor", type=float, default=0.3)
    parser.add_argument("--lr_patience", type=int, default=10)
    parser.add_argument("--max_epochs", type=int, default=500)
    parser.add_argument("--n_workers", type=int, default=12)
    parser.add_argument("--name", type=str, default="MMDF")
    parser.add_argument("--num_image_embeds", type=int, default=1)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--savedir", type=str, default="./savepath/")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n_classes", type=int, default=2)
    parser.add_argument("--annealing_epoch", type=int, default=10)
    parser.add_argument("--device", type=str, default='cpu')
    parser.add_argument("--pretrained_image_encoder", type=bool, default = False)
    parser.add_argument("--freeze_image_encoder", type=bool, default = False)
    parser.add_argument("--pretrained_audio_encoder", type = bool, default=False)
    parser.add_argument("--freeze_audio_encoder", type = bool, default = False)
    parser.add_argument("--augment_dataset", type = bool, default = True)

    for key, value in audio_args.items():
        parser.add_argument(f"--{key}", type=type(value), default=value)

def load_img_modality_model(args):
    rgb_encoder = pytorch_model
    ckpt = torch.load('checkpoints/model.pth', map_location = torch.device('cpu'))
    rgb_encoder.load_state_dict(ckpt['rgb_encoder'], strict = True)
    rgb_encoder.eval()
    return rgb_encoder

def load_spec_modality_model(args):
    spec_encoder = image.RawNet(args)
    ckpt = torch.load('checkpoints/model.pth', map_location = torch.device('cpu'))
    spec_encoder.load_state_dict(ckpt['spec_encoder'], strict = True)
    spec_encoder.eval()
    return spec_encoder


parser = argparse.ArgumentParser(description="Inference models")
get_args(parser)
args, remaining_args = parser.parse_known_args()
assert remaining_args == [], remaining_args
spec_model = load_spec_modality_model(args)
img_model = load_img_modality_model(args)


def preprocess_img(face):
    face = face / 255
    face = cv2.resize(face, (256, 256))
    face_pt = torch.unsqueeze(torch.Tensor(face), dim = 0) 
    return face_pt

def preprocess_audio(audio_file):
    audio_pt = torch.unsqueeze(torch.Tensor(audio_file), dim = 0)
    return audio_pt

def df_spec_pred(input_audio):
    x, _ = input_audio
    audio = preprocess_audio(x)
    spec_grads = spec_model.forward(audio)
    spec_grads_inv = np.exp(spec_grads.cpu().detach().numpy().squeeze())
    max_value = np.argmax(spec_grads_inv)
    if max_value > 0.5:
        preds = round(100 - (max_value*100), 3)
        text2 = f"The audio is REAL."
    else:
        preds = round(max_value*100, 3)
        text2 = f"The audio is FAKE."
    return text2

def df_img_pred(input_image):
    face = preprocess_img(input_image)
    print(f"Face shape is: {face.shape}")
    img_grads = img_model.forward(face)
    img_grads = img_grads.cpu().detach().numpy()
    img_grads_np = np.squeeze(img_grads)
    if img_grads_np[0] > 0.5:
        preds = round(img_grads_np[0] * 100, 3)
        text2 = f"The image is REAL. \nConfidence score is: {preds}"
    else:
        preds = round(img_grads_np[1] * 100, 3)
        text2 = f"The image is FAKE. \nConfidence score is: {preds}"
    return text2


def preprocess_video(input_video, n_frames = 3):
    v_cap = cv2.VideoCapture(input_video)
    v_len = int(v_cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if n_frames is None:
        sample = np.arange(0, v_len)
    else:
        sample = np.linspace(0, v_len - 1, n_frames).astype(int)
    frames = []
    for j in range(v_len):
        success = v_cap.grab()
        if j in sample:
            success, frame = v_cap.retrieve()
            if not success:
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = preprocess_img(frame)
            frames.append(frame)
    v_cap.release()
    return frames

def df_video_pred(input_video):
    video_frames = preprocess_video(input_video)
    real_faces_list = []
    fake_faces_list = []

    for face in video_frames:
        img_grads = img_model.forward(face)
        img_grads = img_grads.cpu().detach().numpy()
        img_grads_np = np.squeeze(img_grads)
        real_faces_list.append(img_grads_np[0])
        fake_faces_list.append(img_grads_np[1])
    real_faces_mean = np.mean(real_faces_list)
    fake_faces_mean = np.mean(fake_faces_list)
    if real_faces_mean > 0.5:
        preds = round(real_faces_mean * 100, 3)
        text2 = f"The video is REAL. \nConfidence score is: {preds}%"
    else:
        preds = round(fake_faces_mean * 100, 3)
        text2 = f"The video is FAKE. \nConfidence score is: {preds}%"

    return text2