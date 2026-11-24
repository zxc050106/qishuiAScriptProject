# 汽水音乐 Android 自动化脚本

基于 AScript 框架的 **Android** 端自动化脚本，用于自动观看汽水音乐广告以获取免费听歌时长。

> iOS 版已存档在 `archive/__init__.ios.py`，如需回到 iOS 版，把该文件复制回根目录改名为 `__init__.py` 即可。

## 功能特性

- 自动启动汽水音乐 App（包名 `com.luna.music`）
- 自动跳过开屏广告（PaddleOCR 精准识别「跳过」）
- 自动清理「续费会员」弹窗
- 自动识别进广告入口：右上角「免」字 / 「立即解锁」「立即领取」弹窗
- 支持两种广告类型：视频广告 + 直播间广告
- 广告结束后自动领取奖励（两步：点「领取成功」→ 点「领取奖励」）
- 无限循环领取模式，直到当日额度用完（连续 10 轮没领到自动退出）
- 全程 OCR + 图像识别为主，不依赖控件树，无需知道 App 内部 id

## 技术栈（Android 命名空间）

| 模块 | 用途 |
| --- | --- |
| `ascript.android.system.open` | 启动 App（`open("com.luna.music")`，本机实测唯一可用启动接口） |
| `ascript.android.system.shell` / `get_foreground_app` | 兜底拉起（`monkey -p`）/ 判断当前前台 App |
| `ascript.android.system.R` / `Device` | 资源路径 / 屏幕尺寸（`Device.display()`） |
| `ascript.android.action` | 点击（无障碍模式直接可用；失败自动回退 HID） |
| `ascript.android.screen.Ocr` | 文字识别（PaddleOCR V5 / ML Kit） |
| `ascript.android.screen.FindImages` | 模板找图（直播间关闭按钮） |
| `ascript.android.node.Selector` | 控件检索（关闭按钮最后兜底） |

## 运行环境

- **平台**：Android（AScript App 安装在手机上）
- **推荐模式**：无障碍模式（开启无障碍权限即可，`action.click` 直接可用）
- **图色模式**：需要配置虚拟 HID / ESP32 物理 HID；脚本已内置 `Hid.click` 回退
- **OCR 引擎**：`paddle`（PP-OCR V5，精度高，默认）或 `mlkit`（速度快）
- **Python**：AScript 自带环境，无需额外安装

## 项目结构

```
qishuiAScriptProject/
├── __init__.py            # 脚本主入口（Android 版，工程约定必须叫这个名字）
├── archive/
│   └── __init__.ios.py    # iOS 原版存档
├── res/
│   └── img/
│       ├── logo.png
│       ├── qishuiyinyue.png           # 汽水音乐桌面图标（兜底启动用，安卓建议重裁）
│       ├── img_zhibojian-close01.png  # 直播间关闭按钮模板图（安卓建议重裁）
│       └── img_1785263403257.png
├── .vscode/
│   └── settings.json       # 项目配置（platform=Android）
├── .trae/
│   └── rules/
│       └── ascript.md      # AScript AI 编码规则
└── .gitignore
```

## 快速开始

### 1. 环境准备

手机上安装 AScript App，打开无障碍权限；确保手机与电脑在同一局域网。

### 2. 设备连接

```bash
# 自动连接（推荐，会扫描局域网 / ADB）
auto_connect()

# 或手动连接
scan_devices()
connect_device(host="192.168.1.x", port=9008)
```

### 3. 首次运行前：校准 OCR 区域（安卓必做）

iOS 版的坐标比例基于 iPhone 1284×2778 实测，安卓机型的界面布局、状态栏高度、按钮位置都不同，
**直接跑很可能识别不到按钮**。因此第一次请按下面流程校准：

1. 打开 `__init__.py`，把顶部 `DEBUG_OCR` 改为 `True`
2. 运行脚本：`run_project(project="qishuiAScriptProject")`
3. 脚本会打印当前屏幕**全部 OCR 文字 + 坐标 + rect**（不做任何点击），例如：

```
[21:20:31]   '领取成功'  @(812,142)  置信度=0.97  rect=[766, 118, 860, 166]
```

4. 对照实际界面，检查这些关键词能否被识别到：
   - `跳过`（开屏广告）
   - `免` / `立即解锁` / `立即领取`（进广告入口）
   - `领取成功` / `领取奖励`（领奖）
   - `元开通`（续费弹窗）
   - `更多直播` / `关注`（直播间广告）
5. 若某个按钮识别不到，按下节「区域校准」调整百分比，或把关键词加进对应 pattern
6. 校准完成后把 `DEBUG_OCR` 改回 `False`，正式运行

### 4. 正式运行

```bash
run_project(project="qishuiAScriptProject")
```

## 脚本流程

```
system.open("com.luna.music")  （首次会弹「想要打开汽水音乐」，选“始终打开”）
    ↓
跳过开屏广告（右上角 OCR「跳过」，5s 内）
    ↓
[用户手动进入第一个广告] 或 [脚本自动找入口]
    ↓
┌───────────────────────────────────────┐
│         核心广告领取循环              │
│                                       │
│  等待广告倒计时结束（最长 30s）        │
│    ├─ 视频广告：等「领取成功」出现     │
│    └─ 直播间广告：关直播间 → 等领取   │
│          ↓                            │
│  步骤 1：点右上角「领取成功」          │
│          ↓                            │
│  步骤 2：点弹窗「领取奖励」→ 进下一轮 │
│          ↓                            │
│  成功次数 +1，重复循环                │
│                                       │
│  连续 10 轮无广告 → 自动退出          │
└───────────────────────────────────────┘
```

## 关键配置

修改 `__init__.py` 顶部常量：

| 常量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_PACKAGE` | `"com.luna.music"` | 汽水音乐安卓包名（优先使用） |
| `APP_NAME` | `"汽水音乐"` | App 名称（仅日志 / 兜底用） |
| `OCR_ENGINE` | `"paddle"` | `"paddle"`（PP-OCR V5，精度高）/ `"mlkit"`（速度快） |
| `SELECTOR_MODE` | `2` | 无障碍控件检索模式：默认 2，取不到试 3 / 1 / 0 |
| `CLICK_CHANNEL` | `"auto"` | `auto` / `action`（无障碍）/ `hid`（图色模式） |
| `DEBUG_OCR` | `False` | 改为 `True` 只打印 OCR 结果，用于校准 |
| `MAX_LOOP_COUNT` | `50` | 最大循环次数（0 = 不限制） |
| `MAX_IDLE_ROUNDS` | `10` | 连续多少轮没领到自动退出 |

## 区域校准

脚本内所有搜索区域都**按屏幕百分比动态计算**（`get_screen_size()` 取实际分辨率），所以换机型不用改绝对坐标，
只需微调百分比。常见需要调的几处（都在 `__init__.py` 里）：

| 位置 | 用途 | 当前比例 |
| --- | --- | --- |
| `skip_splash_ad` 的 `rect_skip` | 开屏「跳过」 | x:78%~99%, y:4%~13% |
| `wait_for_mian_button` 的 `rect_mian` | 右上角「免」字 | x:60%~97%, y:3%~14% |
| `wait_for_mian_button` 的 `rect_unlock` | 「立即解锁 / 领取」大按钮 | x:3%~98%, y:52%~98% |
| `wait_ad_countdown` 的 `rect_claim_top` | 右上角「领取成功」 | x:45%~100%, y:2%~16% |
| `claim_reward` 的 `rect_reward_btn` | 「领取奖励」弹窗按钮 | x:10%~90%, y:22%~75% |
| `close_membership_popup` 的 `bottom_rect` | 底部「元开通」 | x:4%~95%, y:87.7%~96.3% |
| `close_live_room` 的 `rect_close_btn` | 直播间关闭 × | x:60%~100%, y:3%~12% |

调法：`DEBUG_OCR=True` 打印出的 `rect` 就是该按钮在屏幕上的真实像素范围，
按「rect 上界 ÷ 屏幕宽=百分比」换算即可。

## 模板图说明

`res/img/` 下的两张图来自 iOS 设备截图，**安卓分辨率下尺寸不同**：

- `qishuiyinyue.png`（桌面图标）：安卓图标形状 / 圆角 / 背景不同，找图大概率失败；
  这只影响「启动全部失败后的兜底」，正常启动走 `system.open`，不受影响。
- `img_zhibojian-close01.png`（直播间关闭按钮）：`FindImages.find` 内置「模板匹配 → SIFT 全分辨率」两级算法，
  小图标跨分辨率一般还能命中；如果命中率低，**建议在安卓上重新裁一张**替换同名文件。

重裁方法（设备上执行一段 eval）：

```python
from PIL import Image
from ascript.android.system import R, Device
from ascript.android.screen import capture

# 1. 截屏存盘
img = capture()          # Bitmap
img.save("/sdcard/shot.png")
# 2. 用图色工具或看图取关闭按钮的 rect，然后裁图
Image.open("/sdcard/shot.png").crop((x1, y1, x2, y2)).save(R.img("img_zhibojian-close01.png"))
```

## 注意事项

1. **点击通道**：无障碍模式直接用 `action.click`；图色（`screen_only`）模式没有原生点击能力，
   需要 HID 通道（`CLICK_CHANNEL="hid"`），否则点击会静默失败。
2. **不要写 `if __name__ == "__main__"`**：AScript 工程入口不等于 `__main__`，主体代码直接写在顶层。
3. **文案差异**：安卓版文案可能与 iOS 不同（如「免」字入口、弹窗措辞）。
   遇到识别不到，先 `DEBUG_OCR=True` 看实际文字，再把关键词加进对应 `pattern`，不要盲改坐标。
4. **当日额度**：汽水音乐每日有广告领取上限，连续 10 轮没领到会自动退出，不要反复空跑。
5. **OCR 返回格式**：安卓 `Ocr.paddleocr` 返回 `OcrText` 对象（`.text/.center_x/.center_y/.confidence`），
   本脚本已用 `_ocr_to_dict()` 统一成 dict，iOS 版的 `.get()` 写法在安卓上会报错，已修正。
6. **启动接口**：AScript 4.0.17 没有 `ascript.android.app` 模块，`app_start` / `Application.start` 都不存在，
   只能用 `ascript.android.system.open("com.luna.music")`。首次点运行会弹系统确认框，选「始终打开」一次即可。
7. **前台检测（可选）**：`get_foreground_app()` 需要系统「使用情况访问权限」。已在 adb 侧开启：
   `adb shell appops set com.aojoy.airscript GET_USAGE_STATS allow`。
   没开也不影响运行，脚本会跳过前台校验直接往下走。
8. **本机实测环境**：vivo V2359A（Android 16），物理分辨率 1260×2800，
   `Device.display()` 读数正确；广告页右上「领取成功」实测坐标约 (1050, 182)，
   「领取奖励」弹窗实测约 (628, 1495)，区域比例已在该机上验证可用。

## 相关文档

- [AScript 编码规则](.trae/rules/ascript.md)
- [Android OCR 文档](https://www.ascript.cn/docs/android/api/screen/ocr/)
- [Android 找图文档](https://www.ascript.cn/docs/android/api/screen/findimages/)
- [AScript 官方文档](https://www.ascript.cn/docs/android/intro/)
