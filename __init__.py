# -*- coding: utf-8 -*-
"""
汽水音乐 Android 自动化脚本 — 自动看广告获取免费听歌权限
====================================================
平台：Android（AScript 无障碍 / 图色模式）

功能：
  1. 启动汽水音乐 App（包名 com.luna.music）
  2. 开屏广告跳过
  3. 续费会员弹窗清理
  4. 监测"免"字 / "立即解锁"入口，点击进入广告
  5. 等待广告倒计时结束（视频广告 / 直播间广告）
  6. 两步领取奖励：点"领取成功" → 点"领取奖励" → 自动进入下一轮

技术栈（Android）：
  - OCR 文字识别：ascript.android.screen.Ocr（PaddleOCR V5 / MLKit）
  - 图像识别    ：ascript.android.screen.FindImages
  - 动作执行    ：ascript.android.action（点击）
  - 应用管理    ：ascript.android.system.open / shell / get_foreground_app
  - 控件检索    ：ascript.android.node.Selector

与 iOS 版的差异：
  - 命名空间 ascript.ios.* → ascript.android.*
  - 启动 App：app_start(bundle_id) → system.open(package)（无 app 模块，本机实测）
  - 屏幕尺寸：screen_size() → Device.display().widthPixels / heightPixels
  - OCR 返回：OcrText 对象（.text/.center_x/.center_y）→ 本文件统一转成 dict
  - OCR 引擎：iOS 的 "vision" → Android 的 "paddle" / "mlkit"
  - 关闭直播间：固定 iOS 坐标 → 节点 rect 动态计算（适配任意分辨率）
"""

import os
import time
import traceback

# ============== Android 基础模块导入 ==============
from ascript.android import action                              # type: ignore
from ascript.android.system import R, Device                     # type: ignore
from ascript.android.screen import Ocr, FindImages               # type: ignore
from ascript.android.node import Selector                        # type: ignore

# 启动 App / 前台检测（不同 AScript 版本接口不同，这里逐个探测后取用）
# 实测本机 4.0.17：没有 ascript.android.app 模块，
# 可用的是 ascript.android.system 里的 open / shell / get_foreground_app / wait_for_package
try:
    from ascript.android import system as _system                # type: ignore
except Exception:
    _system = None


def _sys_func(name):
    """安全取 system 模块中的接口，取不到返回 None"""
    return getattr(_system, name, None) if _system is not None else None


open_app = _sys_func("open")                 # system.open(package)：打开指定包名的 App
shell = _sys_func("shell")                   # system.shell(cmd)：执行 shell 命令
get_foreground_app = _sys_func("get_foreground_app")   # 当前前台 App
wait_for_package = _sys_func("wait_for_package")       # 等待指定包出现
Noti = _sys_func("Noti")                     # 通知


# ============== 全局配置 ==============

# 汽水音乐 App 名称 / 包名（Android 包名优先，更稳定）
APP_NAME = "汽水音乐"
APP_PACKAGE = "com.luna.music"

# 主循环控制
MAX_LOOP_COUNT = 50         # 最大循环次数（0 = 不限制）
LOOP_INTERVAL = 3           # 每次主循环间隔（秒）
MAX_IDLE_ROUNDS = 10        # 连续多少轮没领到奖励后自动退出

# OCR 引擎（Android：paddle=PP-OCR V5 精度高 / mlkit=谷歌 ML Kit 速度快）
OCR_ENGINE = "paddle"

# 控件检索模式（无障碍模式：2 = SIMPLE|FILTERED，拿不到可试 3 / 1 / 0）
SELECTOR_MODE = 2

# 点击通道：auto=优先 action，失败自动回退 HID；action=仅无障碍；hid=仅 HID（图色模式）
CLICK_CHANNEL = "auto"
CLICK_DUR = 20              # 点击按压时长（毫秒）

# 调试模式：True 时只打印全屏 OCR 结果（用于在安卓机上校准区域），不执行任何点击
DEBUG_OCR = False

# 区域配置（基于屏幕百分比，运行时按实际分辨率计算）
REGION_TOP_RIGHT = None     # 右上角"免"字区域
REGION_FULL = None          # 全屏区域
HAS_LIVE_ROOM_TEMPLATE = False

# 屏幕尺寸缓存
_SCREEN_SIZE = None


# ============== 工具函数 ==============

def log(msg: str):
    """统一日志打印"""
    ts = time.strftime("%H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}")


def get_screen_size():
    """
    获取屏幕尺寸（Android）。

    说明：Android 没有 iOS 的 screen_size() 语义，统一用 Device.display()；
    个别版本可能提供 screen_size()，作为兜底。
    """
    global _SCREEN_SIZE
    if _SCREEN_SIZE:
        return _SCREEN_SIZE

    # 主通道：Device.display()
    try:
        display = Device.display()
        w = int(getattr(display, "widthPixels", 0) or 0)
        h = int(getattr(display, "heightPixels", 0) or 0)
        if w > 0 and h > 0:
            _SCREEN_SIZE = (w, h)
            return _SCREEN_SIZE
    except Exception as e:
        log(f"Device.display 获取屏幕尺寸失败: {e}")

    # 兜底通道：system.screen_size()
    try:
        from ascript.android.system import screen_size  # type: ignore
        size = screen_size()
        if size:
            _SCREEN_SIZE = (int(size[0]), int(size[1]))
            return _SCREEN_SIZE
    except Exception:
        pass

    # 最后兜底：常见安卓分辨率（9:20）
    _SCREEN_SIZE = (1080, 2400)
    log(f"屏幕尺寸获取失败，使用兜底值 {_SCREEN_SIZE[0]}x{_SCREEN_SIZE[1]}，请在 README 中校准")
    return _SCREEN_SIZE


def safe_click(x: int, y: int, desc: str = ""):
    """
    安全点击：带日志 + 异常捕获 + 通道回退。

    安卓上 action.click 依赖无障碍/root 模式；图色（screen_only）模式下
    action.click 不可用，会自动回退到 HID（虚拟/物理 HID 通道）。
    """
    try:
        log(f"点击 [{desc}] -> ({x}, {y})")
        if CLICK_CHANNEL == "hid":
            from ascript.android.action import Hid  # type: ignore
            Hid.click(int(x), int(y))
        else:
            try:
                action.click(int(x), int(y), dur=CLICK_DUR)
            except TypeError:
                # 部分版本 action.click 不支持 dur 参数
                action.click(int(x), int(y))
        time.sleep(0.8)  # 点击后等待界面响应
        return True
    except Exception as e:
        log(f"点击失败 [{desc}]: {e}")
        if CLICK_CHANNEL == "action":
            return False
        # 回退 HID 通道
        try:
            from ascript.android.action import Hid  # type: ignore
            Hid.click(int(x), int(y))
            log(f"  已回退 HID 通道点击 [{desc}]")
            time.sleep(0.8)
            return True
        except Exception as e2:
            log(f"  HID 点击同样失败: {e2}（图色模式请先配置 HID 通道）")
            return False


def click_node(node, desc: str = "") -> bool:
    """
    点击控件节点：优先按节点 rect 计算中心点（跨分辨率安全），失败再走 node.click()。
    """
    if node is None:
        return False
    center = None
    try:
        rect = getattr(node, "rect", None)
        if rect is not None:
            # 形态一：带 x1/y1/x2/y2 的对象
            if hasattr(rect, "x1") and hasattr(rect, "x2"):
                center = ((int(rect.x1) + int(rect.x2)) // 2,
                          (int(rect.y1) + int(rect.y2)) // 2)
            else:
                # 形态二：[left, top, right, bottom]
                vals = list(rect)
                if len(vals) >= 4:
                    center = ((int(vals[0]) + int(vals[2])) // 2,
                              (int(vals[1]) + int(vals[3])) // 2)
    except Exception:
        center = None

    if center and center[0] > 0 and center[1] > 0:
        return safe_click(center[0], center[1], desc)

    # 兜底：节点自带 click()
    try:
        node.click()
        log(f"节点点击 [{desc}]")
        time.sleep(0.8)
        return True
    except Exception as e:
        log(f"节点点击失败 [{desc}]: {e}")
        return False


def _ocr_to_dict(item):
    """
    统一 OCR 结果格式。

    Android 的 Ocr.paddleocr / mlkitocr_v2 返回的是 OcrText 对象
    （.text / .center_x / .center_y / .rect / .confidence），
    而 iOS 版返回 dict。这里统一转成 dict，供后续逻辑复用。
    """
    if item is None:
        return None
    if isinstance(item, dict):
        return dict(item)
    return {
        "text": getattr(item, "text", "") or "",
        "rect": list(getattr(item, "rect", []) or []),
        "center_x": int(getattr(item, "center_x", 0) or 0),
        "center_y": int(getattr(item, "center_y", 0) or 0),
        "confidence": float(getattr(item, "confidence", 0) or 0),
    }


def click_center():
    """点击屏幕中心（用于关闭弹窗的兜底策略）"""
    w, h = get_screen_size()
    safe_click(w // 2, h // 2, "屏幕中心")


def init_regions():
    """根据屏幕尺寸初始化搜索区域，并检查资源文件"""
    global REGION_TOP_RIGHT, REGION_FULL, HAS_LIVE_ROOM_TEMPLATE
    w, h = get_screen_size()
    log(f"屏幕尺寸: {w} x {h}")
    # 右上角区域：x 从 60%~100%，y 从 0%~20%
    REGION_TOP_RIGHT = [int(w * 0.6), 0, w, int(h * 0.2)]
    REGION_FULL = [0, 0, w, h]

    # 检查直播间关闭按钮模板图是否存在
    template_path = R.img("img_zhibojian-close01.png")
    HAS_LIVE_ROOM_TEMPLATE = os.path.exists(template_path)
    log(f"直播间关闭按钮模板图: {'✅ 存在 ' + template_path if HAS_LIVE_ROOM_TEMPLATE else '❌ 不存在（将使用 OCR / 控件兜底方案）'}")


# ============== 调试：打印全屏 OCR 结果 ==============

def dump_all_text():
    """
    打印当前屏幕全部 OCR 文字与坐标。

    用法：把文件顶部 DEBUG_OCR 改成 True，运行一次，把日志发给我（或自己看），
    据此校准本文件里的区域百分比和关键词。这是安卓机与 iPhone 布局差异的必备步骤。
    """
    w, h = get_screen_size()
    log(f"=== 调试：全屏 OCR（屏幕 {w}x{h}，引擎 {OCR_ENGINE}）===")
    try:
        Ocr.set_engine(OCR_ENGINE)
    except Exception as e:
        log(f"set_engine 失败: {e}")
    try:
        items = Ocr.find_all(None)
    except Exception as e:
        log(f"Ocr.find_all 异常: {e}")
        return
    if not items:
        log("未识别到任何文字（检查无障碍/图色权限、引擎模型是否就绪）")
        return
    count = 0
    for raw in items:
        it = _ocr_to_dict(raw)
        count += 1
        log(f"  '{it['text']}'  @({it['center_x']},{it['center_y']})  "
            f"置信度={it['confidence']:.2f}  rect={it['rect']}")
    log(f"=== 共 {count} 条 ===")


# ============== 阶段一：启动 App ==============

def _foreground_desc() -> str:
    """把 get_foreground_app() 的返回值统一成一个可判断的字符串（拿不到返回空串）"""
    if get_foreground_app is None:
        return ""
    try:
        cur = get_foreground_app()
        if isinstance(cur, str):
            s = cur
        else:
            s = ""
            for attr in ("packageName", "package", "_package", "name", "app", "topActivity"):
                v = getattr(cur, attr, None)
                if isinstance(v, str):
                    s = v
                    break
            if not s:
                s = str(cur)
        if s in ("", "None", "null"):
            return ""
        return s
    except Exception as e:
        log(f"get_foreground_app 异常: {e}")
        return ""


def is_app_foreground() -> bool:
    """汽水音乐是否在前台（拿不到信息时返回 False，不阻塞流程）"""
    desc = _foreground_desc()
    if not desc:
        return False
    if APP_PACKAGE in desc or APP_NAME in desc:
        return True
    log(f"  当前前台: {desc}")
    return False


def launch_app() -> bool:
    """启动汽水音乐 App（open → shell → 找图，三层兜底）"""
    log("=" * 50)
    log("阶段一：启动汽水音乐 App")

    if is_app_foreground():
        log("汽水音乐已在前台，无需重复启动")
        return True

    sent = False

    # 通道一：system.open(package)（本机实测可用；首次会弹「想要打开XX」，选始终打开即可）
    if open_app is not None:
        try:
            open_app(APP_PACKAGE)
            sent = True
            log("system.open 已发送启动指令")
        except Exception as e:
            log(f"system.open 启动失败: {e}")

    # 通道二：shell 拉起（open 不可用时兜底）
    if not sent and shell is not None:
        try:
            shell(f"monkey -p {APP_PACKAGE} -c android.intent.category.LAUNCHER 1")
            sent = True
            log("shell monkey 已发送启动指令")
        except Exception as e:
            log(f"shell 启动失败: {e}")

    # 通道三：找图点桌面图标
    if not sent:
        log("启动指令不可用，改为找图点击桌面图标")
        return try_click_app_icon()

    # 等待进入前台（最多 12 秒），只为记录状态，不硬性失败
    deadline = time.time() + 12
    unknown = 0
    while time.time() < deadline:
        time.sleep(1.5)
        desc = _foreground_desc()
        if not desc:
            unknown += 1
            if unknown >= 2:
                log("  读不到前台 App 信息（可在系统设置里给 AScript 开「使用情况访问权限」），跳过前台校验")
                break
            continue
        if APP_PACKAGE in desc or APP_NAME in desc:
            log("汽水音乐已进入前台")
            return True
        log(f"  当前前台: {desc}")

    log("⚠️ 未确认汽水音乐进入前台，继续往下走（若屏幕上已是汽水音乐可忽略此提示）")
    return True


def try_click_app_icon() -> bool:
    """兜底：通过找图点击桌面汽水音乐图标"""
    icon_path = R.img("qishuiyinyue.png")
    log(f"尝试找图点击桌面图标: {icon_path}")
    try:
        for attempt in range(3):
            pos = FindImages.find(icon_path, confidence=0.8)
            if pos:
                return safe_click(pos["center_x"], pos["center_y"], "汽水音乐图标")
            log(f"  第 {attempt+1} 次未找到图标，重试...")
            time.sleep(1)
        log("未找到汽水音乐图标，请检查图片或屏幕内容。")
        return False
    except Exception as e:
        log(f"找图点击图标异常: {e}")
        return False


# ============== 阶段二：全局弹窗清理 ==============

# 弹窗关键词黑名单（出现即尝试关闭）
POPUP_KEYWORDS = [
    "续费会员", "会员享专属", "开通会员", "立即开通",
    "看视频领取", "领取更多时长", "看视频得",
]

# 弹窗关闭按钮关键词
CLOSE_KEYWORDS = [
    "关闭", "叉", "×", "✕", "✖",
    "取消", "以后再说", "稍后", "暂不", "跳过",
]

# ============== 阶段一 B：开屏广告跳过 ==============

# 开屏「跳过」按钮常见文案（带倒计时的 3 秒跳过 / 跳过广告 / 跳过 Xs 等）
SPLASH_SKIP_KEYWORDS = [
    "跳过广告", "跳过",
]


def skip_splash_ad(timeout=5) -> bool:
    """
    启动 App 后立即检测并跳过开屏广告（paddleocr 精准识别版）。
    使用 Ocr.paddleocr 在右上角小区域识别「跳过」文字。
    rect 按屏幕百分比动态计算：x:80%~97%, y:6%~10%（安卓可在 README 中校准）。
    """
    log("阶段一B：检测并跳过开屏广告（paddleocr 精准识别中...）")
    w, h = get_screen_size()
    # 右上角跳过按钮区域（iOS 实测比例，安卓基本一致；如识别不到可放宽）
    rect_skip = [int(w * 0.78), int(h * 0.04), int(w * 0.99), int(h * 0.13)]

    deadline = time.time() + timeout
    scan_count = 0

    while time.time() < deadline:
        scan_count += 1
        try:
            results = Ocr.paddleocr(rect=rect_skip, pattern=r"跳过")
            if results:
                for raw in results:
                    item = _ocr_to_dict(raw)
                    cx, cy = item["center_x"], item["center_y"]
                    if cx > 0 and cy > 0:
                        log(f"  [扫描#{scan_count} paddleocr 命中] '{item['text']}' "
                            f"置信度={item['confidence']:.2f} ({cx},{cy})")
                        safe_click(cx, cy, "开屏跳过")
                        time.sleep(0.2)
                        return True
        except Exception as e:
            log(f"  扫描#{scan_count} paddleocr 异常: {e}")

        time.sleep(0.1)
    log(f"  扫描结束（共 {scan_count} 次），未检测到开屏跳过按钮")
    return False


def ocr_find_text(keywords, rect=None, mode=None, timeout=0):
    """
    在屏幕上查找匹配关键词列表的文字，返回第一个匹配结果 dict 或 None。

    Android 的 Ocr.find(text, rect=None, mode=None, confidence=0.1)，
    其中 mode 是引擎常量（Ocr.MODE_MLK / Ocr.MODE_PADDLE_V2），
    不传则使用 set_engine 设定的默认引擎。
    """
    start = time.time()
    while True:
        try:
            for kw in keywords:
                result = Ocr.find(kw, rect=rect)
                if result:
                    item = _ocr_to_dict(result)
                    log(f"  OCR 命中关键词: '{kw}' -> 实际文本: '{item['text']}' "
                        f"位置: ({item['center_x']}, {item['center_y']})")
                    return item
        except Exception as e:
            log(f"  OCR 识别异常: {e}")
        if timeout <= 0 or (time.time() - start) >= timeout:
            return None
        time.sleep(0.8)


def ocr_find_mid_first(keywords, mode=None):
    """
    【弹窗优先查找】先在屏幕中间区域（高度 20%~85%，弹窗集中区）搜索关键词，
    找不到再全屏搜。避免顶部状态栏的"领取成功×"小字先被命中。
    """
    w, h = get_screen_size()
    mid_rect = [0, int(h * 0.2), w, int(h * 0.85)]
    try:
        for kw in keywords:
            r = Ocr.find(kw, rect=mid_rect)
            if r:
                item = _ocr_to_dict(r)
                log(f"  [中间区命中] '{kw}' -> '{item['text']}' 位置({item['center_x']},{item['center_y']})")
                return item
        for kw in keywords:
            r = Ocr.find(kw, rect=REGION_FULL)
            if r:
                item = _ocr_to_dict(r)
                log(f"  [全屏命中] '{kw}' -> '{item['text']}' 位置({item['center_x']},{item['center_y']})")
                return item
    except Exception as e:
        log(f"  OCR 中间优先查找异常: {e}")
    return None


def paddle_find_first(pattern, rect, log_label=""):
    """
    用 Ocr.paddleocr 在指定小区域精准识别关键词（正则），返回第一个有效命中 dict 或 None。
    Android 的 paddleocr 返回 OcrText 数组，这里统一转 dict。
    """
    try:
        results = Ocr.paddleocr(rect=rect, pattern=pattern)
        if results:
            for raw in results:
                item = _ocr_to_dict(raw)
                if item["center_x"] > 0 and item["center_y"] > 0:
                    if log_label:
                        log(f"  [{log_label}] '{pattern}' -> '{item['text']}' "
                            f"置信度={item['confidence']:.2f} ({item['center_x']},{item['center_y']})")
                    return item
    except Exception as e:
        log(f"  paddle_find '{pattern}' 异常: {e}")
    return None


# 【弹窗内优先点击的按钮】出现这些按钮时，不要打叉关闭，而是优先点击它们
#   - "继续观看"不在这里：它有"中间区(恢复广告)"和"底部(下载诱导)"两种，必须单独识别
PRIORITY_CLICK_KEYWORDS = ["领取奖励", "领取成功", "立即领取", "确认领取"]


def close_membership_popup() -> bool:
    """
    检测并关闭"续费会员"弹窗。
    判定特征：底部出现"元开通"文字（如"12元开通"、"连续包月9元开通"）。
    处理：点击弹窗外部遮罩（屏幕中上方）关闭，不做右滑（避免触发系统返回手势）。
    返回：是否检测到并执行了关闭。
    """
    w, h = get_screen_size()
    bottom_rect = [int(w * 0.044), int(h * 0.877), int(w * 0.953), int(h * 0.963)]
    try:
        results = Ocr.paddleocr(rect=bottom_rect, pattern=r"元开通")
        if results:
            item = _ocr_to_dict(results[0])
            log(f"  [续费会员弹窗] 检测到底部 '{item['text']}'，点击弹窗外部关闭")
            safe_click(int(w * 0.5), int(h * 0.30), "弹窗遮罩")
            time.sleep(1.2)
            return True
    except Exception as e:
        log(f"  续费会员弹窗检测异常: {e}")
    return False


# 兼容旧调用名
check_and_back_from_membership_page = close_membership_popup


def check_and_close_thirdparty_download() -> bool:
    """
    检测是否跳转到第三方应用下载页面（广告结束后可能触发）。
    判定特征：左上角出现"完成"按钮。
    处理：点击"完成"关闭该页面，回到汽水音乐。
    """
    w, h = get_screen_size()
    rect_done = [int(w * 0.02), int(h * 0.06), int(w * 0.25), int(h * 0.12)]
    try:
        results = Ocr.paddleocr(rect=rect_done, pattern=r"完成|关闭|跳过")
        if results:
            item = _ocr_to_dict(results[0])
            cx, cy = item["center_x"], item["center_y"]
            if cx > 0 and cy > 0:
                log(f"  [第三方下载页] 检测到左上角 '{item['text']}'，点击关闭 ({cx},{cy})")
                safe_click(cx, cy, "第三方页关闭")
                time.sleep(1.0)
                return True
    except Exception as e:
        log(f"  第三方下载页检测异常: {e}")
    return False


def dismiss_popups(max_rounds=3) -> int:
    """
    弹窗清理：只处理"续费会员"弹窗（底部"元开通"）。
    返回：已处理的弹窗数量
    """
    log("阶段二：弹窗清理（仅处理续费会员弹窗）")
    handled = 0
    for _ in range(max_rounds):
        if close_membership_popup():
            handled += 1
            time.sleep(1.0)
            continue
        break
    if handled == 0:
        log("  未检测到续费会员弹窗，无需清理。")
    log(f"弹窗清理结束，共处理 {handled} 个")
    return handled


# ============== 阶段三：监测"免"字按钮并进入广告 ==============


def light_background_cleanup() -> bool:
    """轻量级背景清理：只检测续费会员弹窗。paddleocr 小区域识别 ~0.6s，不阻塞主节奏。"""
    return close_membership_popup()


def _detect_membership_popup(rect_pay) -> bool:
    """内部辅助：检测底部是否有"元开通"续费会员弹窗。返回 True=有弹窗。"""
    try:
        results = Ocr.paddleocr(rect=rect_pay, pattern=r"元开通")
        return bool(results)
    except Exception:
        return False


def wait_for_mian_button(timeout=60, allow_mian=True) -> bool:
    """
    监测进广告入口。
      - allow_mian=True（第1轮/开屏后场景）：
          1) 先搜"立即解锁"/"立即领取"弹窗按钮 → 点击
          2) 再搜右上角"免"字 → 点击
             有"元开通"续费弹窗 → 最多重试 2 次（点击 + 验证）；无弹窗 → 点 1 次即返回
          3) 都没有 → 每 8s 清理一次续费会员弹窗
      - allow_mian=False（第2轮及以后）：
          只搜 1) 和 3)，不搜"免"字

    timeout: 最长等待秒数（0 = 只检查一次）
    返回: 是否成功点击进入广告
    """
    if allow_mian:
        log("阶段三：监测进广告入口（优先'立即解锁/领取'弹窗，其次'免'字）")
    else:
        log("阶段三：监测进广告入口（仅'立即解锁/领取'弹窗，不再找'免'字）")

    w, h = get_screen_size()
    # 右上角"免"字区域（安卓状态栏较高，纵向略放宽）
    rect_mian = [int(w * 0.60), int(h * 0.03), int(w * 0.97), int(h * 0.14)]
    # "立即解锁/领取"弹窗区域
    rect_unlock = [int(w * 0.029), int(h * 0.52), int(w * 0.981), int(h * 0.982)]
    # 底部"元开通"续费区
    rect_pay = [int(w * 0.044), int(h * 0.877), int(w * 0.953), int(h * 0.963)]

    deadline = time.time() + timeout if timeout > 0 else 0
    check_count = 0
    last_err_log = 0.0
    last_cleanup = 0.0
    CLEANUP_INTERVAL = 8
    REVERIFY_WAIT = 0.7

    while True:
        check_count += 1

        # ========== 1) 最高优先："立即解锁"/"立即领取" ==========
        item = paddle_find_first(r"立即(解锁|领取)", rect_unlock)
        if item:
            log(f"  [扫描#{check_count} 命中] '{item['text']}' 置信度={item['confidence']:.2f} "
                f"({item['center_x']},{item['center_y']}) → 点击进入广告")
            safe_click(item["center_x"], item["center_y"], f"'{item['text']}'按钮")
            time.sleep(1.5)
            return True

        # ========== 2) allow_mian=True 时搜"免"字 ==========
        if allow_mian:
            mian = paddle_find_first(r"免", rect_mian)
            if mian:
                cx, cy = mian["center_x"], mian["center_y"]
                has_popup = _detect_membership_popup(rect_pay)
                MAX_RETRY = 2 if has_popup else 1
                log(f"  [扫描#{check_count} 命中] '免' -> '{mian['text']}' 置信度={mian['confidence']:.2f} "
                    f"({cx},{cy})  检测到续费弹窗={'是' if has_popup else '否'} → 重试上限={MAX_RETRY}")

                for attempt in range(1, MAX_RETRY + 1):
                    log(f"  [点击尝试 {attempt}/{MAX_RETRY}] '免'字按钮 ({cx},{cy})")
                    safe_click(cx, cy, f"'免'字按钮(尝试{attempt})")

                    if MAX_RETRY == 1:
                        log("  [成功] 无续费会员弹窗，点击 1 次即生效，进入广告")
                        time.sleep(1.0)
                        return True

                    # 验证"免"字是否消失
                    time.sleep(REVERIFY_WAIT)
                    verify = paddle_find_first(r"免", rect_mian)
                    if verify is None:
                        log(f"  [成功] 点击后'免'字消失（尝试 {attempt} 次生效）")
                        time.sleep(0.8)
                        return True
                    log(f"  [未生效] 点击后'免'字仍在屏上 ({verify['center_x']},{verify['center_y']})，继续重试")

                log(f"  [重试耗尽] 连续 {MAX_RETRY} 次点击'免'字未触发跳转，本轮放弃")

        # ========== 3) 都没命中 → 每 8s 清理一次续费会员弹窗 ==========
        now = time.time()
        if now - last_cleanup >= CLEANUP_INTERVAL:
            try:
                light_background_cleanup()
            except Exception as e:
                log(f"  背景清理异常: {e}")
            last_cleanup = now

        if timeout > 0 and time.time() >= deadline:
            log(f"  等待进广告入口超时 ({timeout}s)，本轮跳过")
            return False
        if timeout == 0:
            log("  单次检查：未发现进广告入口")
            return False
        time.sleep(0.2)


# ============== 阶段四：等待广告倒计时结束 ==============

COUNTDOWN_KEYWORDS = ["秒后可领取", "秒后领取", "秒后关闭", "秒后获得"]


def check_and_click_continue_watching() -> bool:
    """
    检测并点击"继续观看"按钮——【仅限屏幕中间显眼位置】。

    重要区分：
      - 中间区（y:25%~68%）：广告中途误关闭后弹出的恢复对话框 → 该点
      - 底部：广告商诱导下载按钮 → 绝不点
    """
    w, h = get_screen_size()
    rect_continue = [int(w * 0.07), int(h * 0.25), int(w * 0.93), int(h * 0.68)]
    item = paddle_find_first(r"继续观看", rect_continue, "继续观看(中间区)")
    if item:
        if safe_click(item["center_x"], item["center_y"], "继续观看(中间区)"):
            time.sleep(2.5)
            return True
    return False


def close_live_room(rect_close_btn) -> bool:
    """
    关闭直播间广告（安卓版关闭逻辑，三层兜底）：
      1) FindImages 模板匹配（需 res/img/img_zhibojian-close01.png，iOS 模板可跨分辨率用 sift 兜底）
      2) OCR 识别 × / 关闭
      3) Selector 控件检索（Android 用 desc/text，坐标由节点 rect 计算，不写死）
    """
    # 第一层：模板匹配
    if HAS_LIVE_ROOM_TEMPLATE:
        try:
            result = FindImages.find(R.img("img_zhibojian-close01.png"), rect=rect_close_btn, confidence=0.7)
            if result:
                log(f"  FindImages 匹配到关闭按钮 (置信度={result.get('confidence', 0):.2f}) "
                    f"@ ({result['center_x']},{result['center_y']}) → 点击")
                if safe_click(result["center_x"], result["center_y"], "直播间关闭(模板)"):
                    time.sleep(1.5)
                    return True
        except Exception as e:
            log(f"  FindImages 匹配异常: {e}，改用 OCR 方案")

    # 第二层：OCR 识别关闭符号
    hit = paddle_find_first(r"[×✕✖]|关闭", rect_close_btn, "关闭按钮OCR")
    if hit:
        log(f"  OCR 命中关闭符号 @ ({hit['center_x']},{hit['center_y']}) → 点击")
        if safe_click(hit["center_x"], hit["center_y"], "直播间关闭(OCR)"):
            time.sleep(1.5)
            return True

    # 第三层：控件检索（Android 版：desc / text 双锚，坐标动态计算）
    try:
        log("  使用 Selector 控件检索兜底（desc/text='关闭'）...")
        node = None
        for selector in (
            Selector(mode=SELECTOR_MODE).desc("关闭"),
            Selector(mode=SELECTOR_MODE).text("关闭"),
        ):
            node = selector.find()
            if node:
                break
        if node:
            log("  Selector 命中'关闭'控件 → 按节点坐标点击")
            if click_node(node, "直播间关闭(Selector)"):
                time.sleep(1.5)
                return True
        else:
            log("  Selector 未找到'关闭'控件")
    except Exception as e:
        log(f"  Selector 控件检索异常: {e}")

    return False


def wait_ad_countdown(timeout=30) -> bool:
    """
    阶段四：在广告界面等待倒计时结束。
    支持两种广告：
      1) 视频广告：倒计时结束 → 右上角出现"领取成功" → 返回，交给 claim_reward
      2) 直播间广告：无倒计时，直接进直播间 → 右上角"更多直播" →
         点关闭按钮(×) → 关闭后出现"领取成功" → 返回
    轮询优先级：领取成功 > 更多直播(关闭直播间) > 倒计时(仅日志)
    """
    log("阶段四：等待广告倒计时结束（支持视频广告 & 直播间广告）")
    w, h = get_screen_size()
    # 右上角"领取成功"区域
    rect_claim_top = [int(w * 0.45), int(h * 0.02), w, int(h * 0.16)]
    # 右上角倒计时文案区域（仅打印日志）
    rect_countdown = [int(w * 0.45), int(h * 0.02), int(w * 0.99), int(h * 0.10)]
    # 直播间右上角"更多直播"区域
    rect_more_live = [int(w * 0.60), int(h * 0.06), w, int(h * 0.16)]
    # 直播间右上角关闭按钮(×)区域
    rect_close_btn = [int(w * 0.60), int(h * 0.03), w, int(h * 0.12)]

    deadline = time.time() + timeout
    last_sec = -1
    checks = 0

    while time.time() < deadline:
        checks += 1

        # ========== 1) 最高优先："领取成功" ==========
        hit_claim = paddle_find_first(r"领取成功", rect_claim_top, "倒计时结束标志")
        if hit_claim:
            log(f"  检测到'领取成功'出现（轮询 {checks} 次）→ 返回，交给 claim_reward")
            return True

        # ========== 2) 次高优先：直播间内需关闭 ==========
        # 这里的「直播间」文字只是记录，不触发关闭（普通广告文案里也会出现这三个字）

        more_live_hit = paddle_find_first(r"更多直播", rect_more_live, "直播间识别")

        # 只用「更多直播」作为直播间判据。
        # 左上角「关注」在普通视频广告里也会被 OCR 误命中（实测本轮每轮都误判并点了 ×，
        # 有提前关闭广告、丢掉本轮奖励的风险），所以只记日志、不再触发关闭。
        if more_live_hit:
            log("  命中'更多直播' → 确认在直播间内，尝试关闭...")
            if close_live_room(rect_close_btn):
                continue  # 关闭后继续循环，等待"领取成功"
            log("  未找到关闭按钮，本轮跳过")

        # ========== 3) 最低优先：倒计时文案 → 仅打印日志 ==========
        cd_hit = paddle_find_first(r"秒后可领奖励|秒后", rect_countdown, "倒计时")
        if cd_hit:
            text = cd_hit.get("text", "")
            sec = None
            for ch in text:
                if ch.isdigit():
                    sec = int(ch)
                    break
            if sec is not None and sec != last_sec:
                log(f"  广告倒计时中... {text}")
                last_sec = sec

        time.sleep(0.8)

    log(f"  等待超时 ({timeout}s)，'领取成功'未出现（本轮可能没有广告可领）")
    return False


# ============== 阶段五：领取奖励 ==============

def claim_reward() -> int:
    """
    领取奖励（确定性两步）：
      倒计时结束 → 右上角"领取成功" → 点击
        → 弹出中间对话框含"领取奖励" → 点击 → 自动进入下一轮广告

    返回：1=两步都成功；0=超时失败
    """
    log("阶段五：领取奖励（两步：点领取成功 → 点领取奖励 → 进入下一轮广告）")
    w, h = get_screen_size()
    # 右上角"领取成功"专属区
    rect_claim_top = [int(w * 0.45), int(h * 0.02), w, int(h * 0.16)]
    # "领取奖励"弹窗按钮专属区
    rect_reward_btn = [int(w * 0.10), int(h * 0.22), int(w * 0.90), int(h * 0.75)]

    # ========== 第一步：等并点右上角"领取成功" ==========
    log("  [步骤 1/2] 等待右上角出现'领取成功'...")
    hit_claim = None
    deadline1 = time.time() + 20
    checks1 = 0
    while time.time() < deadline1:
        checks1 += 1
        hit_claim = paddle_find_first(r"领取成功", rect_claim_top, "领取成功(右上角)")
        if hit_claim:
            break
        time.sleep(0.35)
    if not hit_claim:
        log(f"  [失败] 20s 内未等到右上角'领取成功'（轮询 {checks1} 次），本轮放弃")
        return 0
    log(f"  [命中] '领取成功' @ ({hit_claim['center_x']},{hit_claim['center_y']}) "
        f"置信度={hit_claim.get('confidence', 0):.2f} → 点击")
    safe_click(hit_claim["center_x"], hit_claim["center_y"], "'领取成功'(右上角)")

    # ========== 第二步：等并点"领取奖励"弹窗 ==========
    log("  [步骤 2/2] 等待中间弹窗出现'领取奖励'按钮...")
    time.sleep(0.6)  # 弹窗动画
    hit_reward = None
    deadline2 = time.time() + 15
    checks2 = 0
    while time.time() < deadline2:
        checks2 += 1
        hit_reward = paddle_find_first(r"领取奖励", rect_reward_btn, "领取奖励(弹窗)")
        if hit_reward:
            break
        time.sleep(0.35)
    if not hit_reward:
        log(f"  [失败] 点击'领取成功'后 15s 内未等到'领取奖励'弹窗（轮询 {checks2} 次），本轮放弃")
        return 0
    log(f"  [命中] '领取奖励' @ ({hit_reward['center_x']},{hit_reward['center_y']}) "
        f"置信度={hit_reward.get('confidence', 0):.2f} → 点击（进入下一轮广告）")
    safe_click(hit_reward["center_x"], hit_reward["center_y"], "'领取奖励'(弹窗)")
    time.sleep(1.5)  # 等 App 跳转新广告

    log("  [成功] 两步领取完成，自动进入下一轮广告倒计时")
    return 1


# ============== 主流程 ==============

def run_ad_loop() -> int:
    """
    核心循环：等广告倒计时结束 → 点"领取成功" → 点"领取奖励" → 自动进入下一轮 → 循环
    返回累计成功次数。
    """
    log("\n" + "#" * 60)
    log("🎯 进入核心广告领取循环（停止项目即可退出）")
    log("#" * 60)
    log("   ⚠️  请先在 App 上手动进入一个广告（点右上角'免'字或'立即解锁'弹窗），脚本会自动接管后续轮次")
    success_count = 0
    round_idx = 0
    idle_count = 0

    while True:
        if MAX_LOOP_COUNT > 0 and round_idx >= MAX_LOOP_COUNT:
            log(f"  已达最大循环次数 {MAX_LOOP_COUNT}，退出循环")
            break

        round_idx += 1
        log(f"\n---- 第 {round_idx} 轮广告循环 ----")

        # 步骤 1：等广告倒计时结束（30s 超时）
        got_ad = wait_ad_countdown(timeout=30)
        if not got_ad:
            idle_count += 1
            log(f"  第 {round_idx} 轮：等超时，未检测到广告倒计时结束"
                f"（连续 {idle_count}/{MAX_IDLE_ROUNDS} 轮无广告）")
            if idle_count >= MAX_IDLE_ROUNDS:
                log(f"  ⚠️  连续 {MAX_IDLE_ROUNDS} 轮没领到广告，可能当日额度已用完，自动退出循环")
                break
            time.sleep(3)
            continue

        # 步骤 2：两步领取
        claim_res = claim_reward()
        if claim_res == 1:
            success_count += 1
            idle_count = 0
            log(f"  ✅ 第 {round_idx} 轮领取成功！累计成功次数 = {success_count}")
        else:
            idle_count += 1
            log(f"  ⚠️  第 {round_idx} 轮领取失败（连续 {idle_count}/{MAX_IDLE_ROUNDS} 轮领取失败）")
            if idle_count >= MAX_IDLE_ROUNDS:
                log(f"  ⚠️  连续 {MAX_IDLE_ROUNDS} 轮领取失败，自动退出循环")
                break

        time.sleep(1)

    return success_count


def main():
    """脚本入口主函数（Android 版）"""
    log("🎵 汽水音乐自动看广告脚本（Android 版）启动")
    log("   核心功能：跳过开屏广告 → 等待手动进广告 → 循环领奖励")
    try:
        # 1. 初始化
        init_regions()
        try:
            Ocr.set_engine(OCR_ENGINE)
            log(f"OCR 引擎: {OCR_ENGINE}")
        except Exception as e:
            log(f"OCR 引擎设置失败（将使用默认引擎）: {e}")

        # 调试模式：只打印 OCR 结果，便于在安卓机上校准区域和关键词
        if DEBUG_OCR:
            log("⚠️  DEBUG_OCR = True：仅打印识别结果，不执行点击")
            dump_all_text()
            log("校准完成后把 DEBUG_OCR 改回 False 再运行")
            return

        # 2. 启动 App（失败也继续，避免误判把整轮拦住）
        if not launch_app():
            log("⚠️ 未能自动启动 App，请手动打开汽水音乐后脚本会继续识别")
        # 3. 跳开屏广告
        skip_splash_ad(timeout=5)
        # 4. 核心循环
        total = run_ad_loop()
        # 5. 结束
        log("=" * 60)
        log(f"✅ 脚本运行结束，共成功领取 {total} 次奖励")
        try:
            if Noti is not None:
                Noti.send("汽水音乐安卓版", f"完成，成功 {total} 次")
        except Exception:
            pass
    except Exception as e:
        log(f"❌ 脚本主流程异常: {e}")
        traceback.print_exc()
    finally:
        log("脚本退出")


# ============== 入口：AScript 工程约定，顶层直接执行 ==============
main()
