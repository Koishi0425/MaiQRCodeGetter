import asyncio
import configparser
from contextlib import contextmanager
import ctypes
import collections
import hashlib
import random
import secrets
import ssl
import sys
import traceback
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Tuple
import time
import threading
import uiautomation as auto
from PIL import Image, ImageEnhance
from pyzbar.pyzbar import decode
from aiohttp import http
http.SERVER_SOFTWARE = "cloudflare"

from aiohttp import web, ClientSession
from aiohttp.web_request import BaseRequest

config = configparser.ConfigParser()

if not Path("./config.ini").exists():
    print("=" * 60)
    print("  错误: 未找到 config.ini 配置文件!")
    print("  请复制 config.example.ini 为 config.ini 并修改配置项")
    print("=" * 60)
    time.sleep(2)
    sys.exit(1)

config.read("config.ini", encoding="utf-8")

entryPoint = config.get("Default", "entryPoint", fallback="/login")
loginUserName = config.get("Default", "loginUserName", fallback="admin")
loginPassword = config.get("Default", "loginPassword", fallback="maimaidx")
port = config.getint("Default", "port", fallback=8080)
waitTime = config.getint("Default", "waitTime", fallback=15)
minimize_after_success = config.getint("Default", "minimize_after_success", fallback=0)
capjs_endpoint = config.get("Default", "capjs_endpoint", fallback="")
mode = config.get("Default", "mode", fallback="normal")  # NOQA
certfile = config.get("Default", "certfile", fallback="server.crt")
keyfile = config.get("Default", "keyfile", fallback="server.key")

if hasattr(sys, '_nuitka_binary_dir'):
    PROJECT_ROOT = Path(sys._nuitka_binary_dir)
else:
    PROJECT_ROOT = Path(__file__).parent

STATIC_DIR = PROJECT_ROOT / "static"
ALLOW_EXT = {'.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.html', '.webp', '.avif'}

# ---------- Win32 常量 ----------
SW_RESTORE = 9  # 从最小化还原

# ---------- 加载 user32 ----------
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernal32 = ctypes.windll.kernel32

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM),
                               wintypes.LPARAM]
ShowWindow = user32.ShowWindow
ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
ShowWindow.restype = wintypes.BOOL


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


MAX_VID_COUNT = 50_000          # 内存保护：最多同时缓存多少 visitorId
WINDOW = 300                    # 5 分钟
MAX_FAIL = 10                   # 允许失败次数

_lock = threading.RLock()
_bucket: Dict[str, collections.deque] = {}  # visitorId -> deque[timestamp]


if capjs_endpoint.endswith('/'):
    capjs_endpoint = capjs_endpoint[:-1]

capjs_script = '<script src="/static/js/cap.js"></script>'
cap_widget = f"""\
<div class="form-group"><cap-widget
  id="cap"
  data-cap-api-endpoint="{capjs_endpoint}/api/"
  data-cap-i18n-verifying-label="正在查询你的成分..."
  data-cap-i18n-initial-state="我是纯正wmc"
  data-cap-i18n-solved-label="确实是wmc"
  data-cap-i18n-error-label="我觉得你不像wmc"
  data-cap-i18n-wasm-disabled="启用 WASM 以更快解决验证码"
></cap-widget></div>"""


def _clean_old(ts_deque: collections.deque, now: float) -> None:
    """把窗口外的过期时间戳踢掉"""
    cutoff = now - WINDOW
    while ts_deque and ts_deque[0] < cutoff:
        ts_deque.popleft()


def is_allowed(visitor_id: str) -> Tuple[bool, int]:
    """
    返回 (是否允许, 剩余可用次数)
    """
    global _bucket
    now = time.time()

    with _lock:
        dq = _bucket.get(visitor_id)
        if dq is None:
            if len(_bucket) >= MAX_VID_COUNT:
                oldest_vid = min(_bucket, key=lambda vid: _bucket[vid][0] if _bucket[vid] else 0)
                _bucket.pop(oldest_vid)
            dq = collections.deque()
            _bucket[visitor_id] = dq

        # 清理过期
        _clean_old(dq, now)

        # 当前失败次数
        curr = len(dq)
        allowed = curr < MAX_FAIL
        remain = max(0, MAX_FAIL - curr)
        return allowed, remain


def record_fail(visitor_id: str) -> None:
    """登录失败时调一下，把当前时间戳塞进去"""
    with _lock:
        dq = _bucket.get(visitor_id)
        if dq is None:
            dq = collections.deque()
            _bucket[visitor_id] = dq
        dq.append(time.time())


def reset(visitor_id: str) -> None:
    """登录成功后清掉该设备计数器"""
    with _lock:
        _bucket.pop(visitor_id, None)
    

@contextmanager
def keepScreenWakeup():
    """保持屏幕唤醒"""
    kernal32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
    try:
        yield
    finally:
        kernal32.SetThreadExecutionState(0x80000000)


with open(STATIC_DIR / "qrcode.html", "r", encoding="utf-8") as f:
    qrcodeHtml = f.read()

if loginPassword == "maimaidx":
    warning_str = ("*****************************************************\n"
                   "*****************************************************\n"
                   "**     WARNING: Default Weak Password detected!    **\n"
                   "**      YOU SHOULD NOT USE IN PUBLIC NETWORK!      **\n"
                   "**            警告: 检测到默认的弱密码!            **\n"
                   "**      你**绝对**不可以将程序暴露在公网上!!!      **\n"
                   "*****************************************************\n"
                   "*****************************************************\n"
                   )
    print(warning_str)
if entryPoint == "/login":
    warning_str = ("*****************************************************\n"
                   "*****************************************************\n"
                   "**      WARNING: Default Login Entry detected!     **\n"
                   "**      Default Entry Point may cause ATTACK!!     **\n"
                   "**           警告: 检测到默认的登录入口!           **\n"
                   "**       使用默认的登录入口可能会遭到爆破!!!       **\n"
                   "*****************************************************\n"
                   "*****************************************************\n"
                   )
    print(warning_str)
if port < 1 or port > 65535:
    error_str = ("*****************************************************\n"
                 "*****************************************************\n"
                 "**         Error: Port value is not vaild!         **\n"
                 "**     Port must be a int between 1 and 65535!!    **\n"
                 "**                 错误: 端口不合法!               **\n"
                 "**     端口必须是一个位于1和65535之间的数字!!!     **\n"
                 "*****************************************************\n"
                 "*****************************************************\n"
                 )
    print(error_str)
    time.sleep(2)
    sys.exit(1)
on_active = 0
active_sessions = {}  # 存储活跃会话
used_token = collections.deque(maxlen=1000)  # 存储已使用的token


def find_window_handle(title_part: str, exact=False) -> int:
    """
    通过标题（部分）查找顶层窗口句柄，忽略大小写
    :param title_part: 标题关键词
    :param exact: True=完全匹配，False=包含即可
    :return: 找到返回 hwnd，找不到返回 0
    """
    result = 0
    title_part = title_part.lower()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _):
        nonlocal result
        if user32.IsWindowVisible(hwnd) and user32.IsWindowEnabled(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            txt = buf.value.lower()
            if (exact and txt == title_part) or (not exact and title_part in txt):
                result = hwnd
                return False  # 停止枚举
        return True  # 继续枚举

    user32.EnumWindows(enum_proc, 0)
    return result


def mark_str(input_str, percentage=80, replace_char='*'):
    if not input_str:
        return input_str

    percentage = max(0, min(100, percentage))
    str_list = list(input_str)
    length = len(str_list)
    num_to_replace = int(length * percentage / 100)
    start = (length - num_to_replace) // 2
    end = start + num_to_replace
    start = max(0, start)
    end = min(length, end)
    for i in range(start, end):
        str_list[i] = replace_char
    return ''.join(str_list)


def generate_cookie_value(username, password):
    return hashlib.sha256(f"{username}:{password}".encode()).hexdigest()


def generate_session_token():
    """生成安全的会话令牌"""
    return secrets.token_urlsafe(32)


def is_valid_session(session_token):
    """验证会话是否有效"""
    return session_token in active_sessions


def create_session(username):
    """创建新的会话"""
    session_token = generate_session_token()
    active_sessions[session_token] = {
        'username'     : username,
        'created_at'   : datetime.now().isoformat(),
        'last_activity': datetime.now().isoformat()
    }
    return session_token


def cleanup_expired_sessions():
    """清理过期会话（7天无活动）"""
    current_time = datetime.now()
    expired_sessions = []

    for session_token, session_data in active_sessions.items():
        last_activity = datetime.fromisoformat(session_data['last_activity'])
        if current_time - last_activity > timedelta(days=7):
            expired_sessions.append(session_token)

    for session_token in expired_sessions:
        del active_sessions[session_token]


async def auth_middleware(_, handler):
    async def middleware_handler(request):
        # 允许访问登录页面和静态资源
        if request.path in [entryPoint, '/204'] or request.path.startswith('/static/'):
            return await handler(request)

        session_token = request.cookies.get('session_token')

        # 清理过期会话
        cleanup_expired_sessions()

        if not session_token or not is_valid_session(session_token):
            if entryPoint == '/login':  # 默认入口则重定向，否则返回404
                response = web.HTTPTemporaryRedirect(entryPoint)
                response.del_cookie('session_token')
                return response
            return web.HTTPNotFound()

        # 更新最后活动时间
        if session_token in active_sessions:
            active_sessions[session_token]['last_activity'] = datetime.now().isoformat()

        return await handler(request)

    return middleware_handler


async def login_handler(_):
    """显示登录页面"""
    try:
        with open(STATIC_DIR / "login.html", "r", encoding="utf-8") as file:
            login_html = file.read()
        if capjs_endpoint:
            login_html = login_html.replace("<!-- CAP Worker Js Replace -->", capjs_script).replace("<!-- cap-widget -->", cap_widget)
        return web.Response(text=login_html, content_type='text/html')
    except FileNotFoundError:
        return web.Response(text="Login page not found", status=404)


async def post_login_handler(request):
    """处理登录请求"""
    try:
        # 处理表单数据
        if request.content_type and 'multipart/form-data' in request.content_type:
            form_data = await request.post()
            username = form_data.get('username', '').strip()
            password = form_data.get('password', '').strip()
            token = form_data.get('token', '').strip()
            fp = form_data.get('fp', '').strip()
        else:
            # 处理查询参数
            username = request.query.get('username', '').strip()
            password = request.query.get('password', '').strip()
            token = request.query.get('token', '').strip()
            fp = request.query.get('fp', '').strip()

        if not fp:
            return web.json_response({'success': False, 'error': f'登录失败次数过多，请{WINDOW}秒后重试'})
        allowed, _ = is_allowed(fp)
        if not allowed:
            return web.json_response({'success': False, 'error': f'登录失败次数过多，请{WINDOW}秒后重试'})
        if capjs_endpoint:
            if not token or token in used_token:
                record_fail(fp)
                return web.json_response({'success': False, 'error': '用户名或密码错误'})
            used_token.append(token)
            async with ClientSession() as session:
                try:
                    async with session.post(f'{capjs_endpoint}/api/validate', json={
                        "token": token,
                        "keepToken": False
                    }) as res:
                        print(await res.text())
                        if res.ok:
                            data = await res.json()
                            if not data['success']:
                                record_fail(fp)
                                return web.json_response({'success': False, 'error': '用户名或密码错误'})
                        else:
                            record_fail(fp)
                            return web.json_response({'success': False, 'error': '用户名或密码错误'})
                except Exception:
                    traceback.print_exc()
                    record_fail(fp)
                    return web.json_response({'success': False, 'error': '用户名或密码错误'})

        if not username or not password:
            if request.headers.get('Accept') == 'application/json':
                record_fail(fp)
                return web.json_response({'success': False, 'error': '用户名和密码不能为空'})
            else:
                return web.HTTPSeeOther(entryPoint)

        if username == loginUserName and password == loginPassword:
            # 创建会话
            session_token = create_session(username)

            # 返回JSON响应（AJAX请求）
            if request.headers.get('Accept') == 'application/json':
                response = web.json_response({'success': True, 'session_token': session_token, 'redirect': '/'})
            else:
                # 返回重定向响应（表单提交）
                response = web.HTTPTemporaryRedirect("/")

            # 设置会话cookie
            response.set_cookie(
                'session_token',
                session_token,
                httponly=True,
                max_age=60 * 60 * 24 * 7,  # 7天
                secure=False,  # 开发环境设为False，生产环境应设为True
                samesite='Lax'
            )
            reset(fp)
            return response

        record_fail(fp)
        return web.json_response({'success': False, 'error': '用户名或密码错误'})

    except Exception:
        traceback.print_exc()
        return web.json_response({'success': False, 'error': '登录处理失败'})


async def logout_handler(request):
    """处理注销请求"""
    session_token = request.cookies.get('session_token')

    if session_token and session_token in active_sessions:
        del active_sessions[session_token]

    response = web.HTTPTemporaryRedirect(entryPoint)
    response.del_cookie('session_token')
    return response


def component_screenshot(comp: auto.Control) -> Image.Image:
    """
    comp: 任意 uiautomation 组件
    返回: Pillow Image（组件级，无视遮挡/最小化）
    """
    # 1. 组件在屏幕上的绝对坐标
    rect = comp.BoundingRectangle
    scr_left, scr_top, scr_right, scr_bottom = rect.left, rect.top, rect.right, rect.bottom
    scr_width = scr_right - scr_left
    scr_height = scr_bottom - scr_top

    # 2. 窗口句柄 & 窗口坐标
    hwnd = wintypes.HWND(comp.GetTopLevelControl().NativeWindowHandle)
    win_rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(win_rect))
    win_left, win_top = win_rect.left, win_rect.top

    # 3. 组件相对窗口的偏移
    rel_left = scr_left - win_left
    rel_top = scr_top - win_top

    # 4. 创建窗口级内存位图
    hdc_wnd = wintypes.HDC(user32.GetWindowDC(hwnd))
    hdc_mem = wintypes.HDC(gdi32.CreateCompatibleDC(hdc_wnd))
    bmp_mem = wintypes.HBITMAP(gdi32.CreateCompatibleBitmap(hdc_wnd,
                                                            win_rect.right - win_rect.left,
                                                            win_rect.bottom - win_rect.top))
    old_obj = wintypes.HGDIOBJ(gdi32.SelectObject(hdc_mem, bmp_mem))

    # 5. 把窗口内容画到内存位图（无视遮挡）
    user32.PrintWindow(hwnd, hdc_mem, 0)

    # 6. 准备 BITMAPINFO
    win_w = win_rect.right - win_rect.left
    win_h = win_rect.bottom - win_rect.top

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    bmi = BITMAPINFOHEADER()
    ctypes.memset(ctypes.byref(bmi), 0, ctypes.sizeof(bmi))
    bmi.biSize = ctypes.sizeof(bmi)
    bmi.biWidth, bmi.biHeight = win_w, -win_h  # top-down
    bmi.biPlanes, bmi.biBitCount = 1, 32

    buf_len = win_w * win_h * 4
    buffer = ctypes.create_string_buffer(buf_len)
    gdi32.GetDIBits(hdc_mem, bmp_mem, 0, win_h, buffer, bmi, 0)

    # 7. 清理 GDI 资源
    gdi32.SelectObject(hdc_mem, old_obj)
    gdi32.DeleteObject(bmp_mem)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_wnd)

    # 8. 得到整窗口 Pillow 图
    img = Image.frombuffer('RGBA', (win_w, win_h), buffer, 'raw', 'BGRA', 0, 1)  # NOQA

    # 9. 裁剪组件区域
    crop = img.crop((rel_left, rel_top,
                     rel_left + scr_width,
                     rel_top + scr_height))
    return crop


def is_valid_qrcode(image):
    gray = image.convert('L')
    decoded = decode(gray)
    return len(decoded) > 0


def convertImageL(comp: auto.Control) -> Image:
    screenshot = component_screenshot(comp)
    enhancer = ImageEnhance.Contrast(screenshot)
    screenshot = enhancer.enhance(2.0)
    screenshot = screenshot.convert('L')
    return screenshot


def main():
    global on_active
    if mode == "web_only":
        now = datetime.now()
        dt = now.strftime("%y%m%d%H%M%S")
        code = f"MAID{dt}" + hashlib.sha256(str(time.time()).encode()).hexdigest().upper()
        print(f"Code: {code}")
        dt = now + timedelta(minutes=10)
        expTimeStr = dt.strftime("%m/%d %H:%M")
        return code, expTimeStr, round(random.uniform(2.0, 4.6), 2)
    print(f"[QR] 开始获取二维码... (超时上限 {waitTime}s)")
    handleTime = time.time()
    last_code = None
    if on_active and time.time() - on_active < waitTime + 10:
        elapsed = time.time() - on_active
        msg = f"[QR] 并发限制: 距上次获取仅 {elapsed:.1f}s，需等待 {waitTime + 10}s 后方可重试"
        print(msg)
        raise RuntimeError(msg)
    on_active = handleTime
    hwnd = find_window_handle("舞萌丨中二")
    if not hwnd:
        on_active = 0
        raise RuntimeError("[QR] 错误: 未找到「舞萌丨中二」窗口，请确认公众号窗口已打开")
    nowFocusWindow = auto.GetForegroundControl()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    maimaiWindow = auto.WindowControl(searchDepth=1, Name="舞萌丨中二")
    if not maimaiWindow.Exists(0, 0):
        on_active = 0
        raise RuntimeError("无法获取 舞萌丨中二 窗口元素")
    btn = maimaiWindow.ButtonControl(Name="玩家二维码")
    messages = maimaiWindow.ListControl(Name="消息", ClassName="mmui::RecyclerListView").GetChildren()
    reqTime = datetime.now()
    year = str(reqTime.year - 2000).zfill(2)
    month = str(reqTime.month).zfill(2)
    day = str(reqTime.day).zfill(2)
    hour = str(reqTime.hour).zfill(2)
    minute = str(reqTime.minute).zfill(2)
    second = str(reqTime.second).zfill(2)
    if messages:
        screenshot = convertImageL(messages[-1])
        if is_valid_qrcode(screenshot):
            last_code = decode(screenshot)[0].data.decode('utf-8')[4:]
            if mode == "demo":
                print(
                    f"Last exist QR code: MAID{year}{month}{day}{hour}{minute}{second}{hashlib.sha256(last_code.encode()).hexdigest().upper()}")
            else:
                print(f"Last QR code: {last_code if mode != 'marked' else mark_str(last_code)}")
    if btn.Exists(0, 0) and mode != "demo":
        try:
            t = time.time()
            while user32.GetForegroundWindow() != hwnd and time.time() - t < 1:
                user32.ShowWindow(hwnd, 5)
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 2 | 1)
                user32.SetForegroundWindow(hwnd)
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rx, ry = pt.x, pt.y
            btn.Click(simulateMove=False, waitTime=0)
            user32.SetCursorPos(rx, ry)
            print('Clicked!')
        finally:
            ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0,
                                              0x0001 | 0x0002 | 0x0020)
            if nowFocusWindow is not None:
                nowFocusWindow.SetFocus()
    now = time.time()
    messagesControl = maimaiWindow.ListControl(Name="消息", ClassName="mmui::RecyclerListView")
    while time.time() - now < waitTime:
        messages = messagesControl.GetChildren()
        message = messages[-1] if messages else None
        if mode == "demo" and message:
            messageBox = message
            break
        if not message:
            time.sleep(0.1)
            continue
        screenshot = convertImageL(message)
        if is_valid_qrcode(screenshot):
            if not last_code:
                messageBox = message
                break
            if last_code != decode(screenshot)[0].data.decode('utf-8')[4:]:
                messageBox = message
                break
        time.sleep(0.1)
    else:
        on_active = 0
        print(messages)
        raise Exception(
            f"[QR] 获取超时: 在 {waitTime}s 内未能从微信窗口截取到新二维码。"
            f"请确认: 1) 公众号窗口未关闭 2) 二维码消息可见 3) 网络正常(可尝试增大 config.ini 中的 waitTime)"
        )
    if messageBox and messageBox.Exists():
        now = time.time()
        while time.time() - now < waitTime:
            screenshot = convertImageL(messageBox)
            # screenshot.show()
            if is_valid_qrcode(screenshot):
                print("Valid QR code detected!")
                break
            else:
                print("Invalid QR code detected, retrying...")
                time.sleep(0.5)
        else:
            on_active = 0
            print(f"Failed to detect QR code in {waitTime} seconds.")
            return None
        if nowFocusWindow is not None:
            nowFocusWindow.SetFocus()
            if minimize_after_success:
                ctypes.windll.user32.ShowWindow(hwnd, 7)
        code = decode(screenshot)[0].data.decode('utf-8')[4:]
        if mode == "demo":
            code = f"MAID{year}{month}{day}{hour}{minute}{second}" + hashlib.sha256(code.encode()).hexdigest().upper()
        print(f"Code: {code if mode != 'marked' else mark_str(code)}")
        dt = datetime.strptime(code[4:16], "%y%m%d%H%M%S")
        dt += timedelta(minutes=10)
        expTimeStr = dt.strftime("%m/%d %H:%M")

        sptTime = round(time.time() - handleTime, 2)
        print(f"Completed in {sptTime:.2f} s.")
        on_active = 0
        return code, expTimeStr, sptTime
    else:
        maimaiWindow.SetTopmost(False)
        if mode == "demo":
            code = f"MAID{year}{month}{day}{hour}{minute}{second}" + hashlib.sha256(
                "HOMO114514".encode()).hexdigest().upper()
            print(f"Code: {code}")
            expTimeStr = (reqTime + timedelta(minutes=10)).strftime("%m/%d %H:%M")

            sptTime = round(time.time() - handleTime, 2)
            print(f"Completed in {sptTime:.2f} s.")
            on_active = 0
            return code, expTimeStr, sptTime
        print("[QR] 错误: 消息列表为空，未能找到二维码消息元素")
        if nowFocusWindow is not None:
            nowFocusWindow.SetFocus()
        on_active = 0
        return None


async def handle(_):
    try:
        res = main()
        if res:
            return web.json_response({'success': True, 'maid': res[0], 'time': res[1], 'spend': res[2]})
        return web.json_response({'success': False, 'error': '未能获取二维码，请查看控制台日志了解详情'}, status=500)
    except Exception as e:
        tb_str = traceback.format_exc()
        err_str = f"{e.__class__.__name__}: {e}"
        return web.json_response({'success': False, 'error': err_str, 'traceback': tb_str}, status=500)


async def htmlPage(_: BaseRequest):
    """主页 — 纯二维码展示页（前端通过 AJAX 调用 /maimai 获取二维码）"""
    return web.Response(text=qrcodeHtml, content_type='text/html')


async def handlePostRoot(_: BaseRequest):
    if entryPoint == '/login':
        response = web.HTTPSeeOther(entryPoint)
        response.del_cookie('session_token')
        return response
    return web.HTTPNotFound()


async def QRCodePage(_: BaseRequest):
    """二维码页面（前端通过 AJAX 调用 /maimai 获取二维码）"""
    return web.Response(text=qrcodeHtml, content_type='text/html')
    

async def handle_204(_: BaseRequest):
    return web.Response(status=204)


async def static_handler(request: web.Request):
    rel_path = request.match_info['filepath']
    file_path = STATIC_DIR / rel_path
    if file_path.suffix.lower() not in ALLOW_EXT:
        raise web.HTTPForbidden()
    print(f"Serving static file: {file_path}")
    return web.FileResponse(file_path)


async def clear_sessions(_: BaseRequest):
    active_sessions.clear()
    return web.Response(text='Clear sessions success.')


async def web_server():
    app = web.Application(middlewares=[auth_middleware])  # NOQA
    app.router.add_get('/maimai', handle)
    app.router.add_get(entryPoint, login_handler)
    app.router.add_post(entryPoint, post_login_handler)
    app.router.add_get('/logout', logout_handler)
    app.router.add_get('/logout_all', clear_sessions)
    app.router.add_get('/qrc', QRCodePage)
    app.router.add_get('/qrcode', QRCodePage)
    app.router.add_get('/204', handle_204)
    app.router.add_get('/', htmlPage)
    app.router.add_post('/', handlePostRoot)
    app.router.add_get('/static/{filepath:.*}', static_handler, name='static')
    runner = web.AppRunner(app)
    await runner.setup()
    if Path(certfile).exists() and Path(keyfile).exists():
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        site = web.TCPSite(runner, '0.0.0.0', port, ssl_context=ssl_context)
        print("SSL enabled")
        try:
            await site.start()
        except OSError as e:
            if 'error while attempting to bind on address':
                print(f"Address already in use: {e}")
                time.sleep(2)
                sys.exit(1)
        print(f'Server started at https://localhost:{port}')
    else:
        site = web.TCPSite(runner, '0.0.0.0', port)
        try:
            await site.start()
        except OSError as e:
            if 'error while attempting to bind on address':
                print(f"Address already in use: {e}")
                time.sleep(2)
                sys.exit(1)
        print(f'Server started at http://localhost:{port}')

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("\nServer is shutting down...")
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    try:
        with keepScreenWakeup():
            if len(sys.argv) > 1:
                main()
            else:
                asyncio.run(web_server())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
