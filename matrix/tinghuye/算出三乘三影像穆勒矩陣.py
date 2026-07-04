import os
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image


def linear_stokes(angle_deg):
    theta = np.deg2rad(angle_deg)
    return np.array([
        1.0,
        np.cos(2 * theta),
        np.sin(2 * theta)
    ], dtype=np.float64)


def read_image_as_float(path):
    img = Image.open(path)
    img = np.array(img)

    if img.ndim == 3:
        img = img[..., :3]
        img = np.mean(img, axis=2)

    return img.astype(np.float64)


def build_system_matrix(generator_angles, analyzer_angles):
    """
    I = A @ M @ S
    若 M 以 row-major 展開，係數為 kron(A, S)
    順序固定為:
    A0_G0, A0_G45, A0_G90,
    A45_G0, A45_G45, A45_G90,
    A90_G0, A90_G45, A90_G90
    """
    H = []
    for a_deg in analyzer_angles:
        A = linear_stokes(a_deg)
        for g_deg in generator_angles:
            S = linear_stokes(g_deg)
            H.append(np.kron(A, S))
    return np.array(H, dtype=np.float64)


def solve_mueller_image_3x3(image_stack, generator_angles, analyzer_angles):
    if image_stack.shape[0] != 9:
        raise ValueError("image_stack 第一維必須是 9")

    Hmat = build_system_matrix(generator_angles, analyzer_angles)
    Hinv = np.linalg.pinv(Hmat)

    n_meas, H, W = image_stack.shape
    b = image_stack.reshape(n_meas, -1)   # (9, H*W)
    m_vec = Hinv @ b                      # (9, H*W)
    M_img = m_vec.T.reshape(H, W, 3, 3)

    return M_img


def normalize_mueller_by_m00(M_img, eps=1e-12):
    m00 = M_img[:, :, 0, 0]
    return M_img / (m00[:, :, None, None] + eps)


def find_existing_file(input_dir, base_name, ext_priority):
    for ext in ext_priority:
        path = os.path.join(input_dir, base_name + ext)
        if os.path.exists(path):
            return path
    return None


def load_9_images_by_naming(input_dir, angles=(0, 45, 90),
                            ext_priority=(".tiff", ".tif", ".png", ".bmp")):
    """
    你的命名方式:
    0_45 = G0_A45

    但反解需要的 image_stack 順序是:
    A0_G0, A0_G45, A0_G90,
    A45_G0, A45_G45, A45_G90,
    A90_G0, A90_G45, A90_G90

    所以讀檔時要轉成 base_name = f"{g}_{a}"
    """
    generator_angles = list(angles)
    analyzer_angles = list(angles)

    image_list = []
    used_files = []
    shape_ref = None

    for a in analyzer_angles:
        for g in generator_angles:
            base_name = f"{g}_{a}"   # 因為你的命名是 G_A
            file_path = find_existing_file(input_dir, base_name, ext_priority)

            if file_path is None:
                raise FileNotFoundError(f"找不到檔案: {base_name}，支援副檔名 {ext_priority}")

            img = read_image_as_float(file_path)

            if shape_ref is None:
                shape_ref = img.shape
            elif img.shape != shape_ref:
                raise ValueError(f"影像尺寸不一致: {file_path}, shape={img.shape}, expected={shape_ref}")

            image_list.append(img)
            used_files.append(file_path)

    image_stack = np.stack(image_list, axis=0)
    return image_stack, used_files


def save_element_maps(M_img, out_dir, prefix="M", cmap="jet", fixed_range=None):
    os.makedirs(out_dir, exist_ok=True)

    for i in range(3):
        for j in range(3):
            elem = M_img[:, :, i, j]

            np.save(os.path.join(out_dir, f"{prefix}_{i}{j}.npy"), elem)
            Image.fromarray(elem.astype(np.float32)).save(os.path.join(out_dir, f"{prefix}_{i}{j}.tiff"))

            plt.figure(figsize=(6, 5))
            if fixed_range is None:
                plt.imshow(elem, cmap=cmap)
            else:
                plt.imshow(elem, cmap=cmap, vmin=fixed_range[0], vmax=fixed_range[1])
            plt.colorbar()
            plt.title(f"{prefix}[{i}{j}]")
            plt.tight_layout()
            plt.savefig(os.path.join(out_dir, f"{prefix}_{i}{j}.png"), dpi=200)
            plt.close()


def save_3x3_overview(M_img, out_path, title="3x3 Image Mueller Matrix", cmap="jet", fixed_range=None):
    fig, axes = plt.subplots(3, 3, figsize=(12, 10))

    for i in range(3):
        for j in range(3):
            ax = axes[i, j]
            elem = M_img[:, :, i, j]

            if fixed_range is None:
                im = ax.imshow(elem, cmap=cmap)
            else:
                im = ax.imshow(elem, cmap=cmap, vmin=fixed_range[0], vmax=fixed_range[1])

            ax.set_title(f"M[{i}{j}]")
            ax.axis("off")
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle(title, fontsize=16)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()


if __name__ == "__main__":
    # =========================
    # 1. 路徑設定
    # =========================
    input_dir = r"./input_images"
    output_dir = r"./mueller_output"

    angles = (0, 45, 90)
    ext_priority = (".tiff", ".png", ".bmp")

    # 若有 dark image 可填入，否則設 None
    dark_image_path = None

    os.makedirs(output_dir, exist_ok=True)

    # =========================
    # 2. 讀取 9 張影像
    # =========================
    image_stack, used_files = load_9_images_by_naming(
        input_dir=input_dir,
        angles=angles,
        ext_priority=ext_priority
    )

    print("實際使用的檔案：")
    for f in used_files:
        print(f)

    print("image_stack shape =", image_stack.shape)

    _, H, W = image_stack.shape
    print(f"Image size = {W} x {H}")

    # =========================
    # 3. 背景扣除
    # =========================
    if dark_image_path is not None:
        dark = read_image_as_float(dark_image_path)
        if dark.shape != (H, W):
            raise ValueError(f"dark image 尺寸不符: {dark.shape} vs {(H, W)}")
        image_stack = image_stack - dark[None, :, :]
        image_stack = np.clip(image_stack, 0, None)

    # =========================
    # 4. 解 3x3 Image Mueller Matrix
    # =========================
    generator_angles = [0, 45, 90]
    analyzer_angles = [0, 45, 90]

    M_img = solve_mueller_image_3x3(
        image_stack=image_stack,
        generator_angles=generator_angles,
        analyzer_angles=analyzer_angles
    )

    np.save(os.path.join(output_dir, "M_img.npy"), M_img)

    # =========================
    # 5. m00 正規化
    # =========================
    M_norm = normalize_mueller_by_m00(M_img)
    np.save(os.path.join(output_dir, "M_norm.npy"), M_norm)

    # =========================
    # 6. 輸出 individual maps
    # =========================
    save_element_maps(
        M_img,
        out_dir=os.path.join(output_dir, "raw_elements"),
        prefix="M",
        cmap="jet",
        fixed_range=None
    )

    save_element_maps(
        M_norm,
        out_dir=os.path.join(output_dir, "normalized_elements"),
        prefix="Mnorm",
        cmap="jet",
        fixed_range=(-1, 1)
    )

    # =========================
    # 7. 輸出 overview
    # =========================
    save_3x3_overview(
        M_img,
        out_path=os.path.join(output_dir, "Mueller_3x3_raw_overview.png"),
        title="3x3 Image Mueller Matrix (Raw)",
        cmap="jet",
        fixed_range=None
    )

    save_3x3_overview(
        M_norm,
        out_path=os.path.join(output_dir, "Mueller_3x3_normalized_overview.png"),
        title="3x3 Image Mueller Matrix (Normalized by m00)",
        cmap="jet",
        fixed_range=(-1, 1)
    )

    # =========================
    # 8. 印出中心 pixel 數值
    # =========================
    cy, cx = H // 2, W // 2
    print("\nCenter pixel Mueller matrix (raw):")
    print(np.round(M_img[cy, cx], 4))

    print("\nCenter pixel Mueller matrix (normalized):")
    print(np.round(M_norm[cy, cx], 4))

    print("\n完成")
    print("M_img.npy 已輸出到:", os.path.join(output_dir, "M_img.npy"))
    print("M_norm.npy 已輸出到:", os.path.join(output_dir, "M_norm.npy"))