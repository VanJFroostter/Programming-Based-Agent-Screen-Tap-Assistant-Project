#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练程序.py —— 程序1（训练程序，带可视化界面）

生成链: 程序1(本程序) → 自动生成 程序2(主程序.py) → 自动生成 程序3(run_program.py)

界面显示「您要完成什么任务？」，用户输入任务文字后，一键完成:
  1. 命令1 → LLM → 结果1（以 json 定义 state/choice 及严格识别标准）
  2. 命令2 → LLM → 结果2（state→choice 的运算流程程序）
  3. 命令3 → LLM → 结果3（脚本/Agent 分类程序）
  4. 新建文件夹，并在其中建立 subfolder 和 Memory（保存 结果1/2/3）
  5. 自动生成 程序2 = 主程序.py（内嵌 结果1/2/3 与 LLM 配置，可独立运行）
  6. 自动运行 程序2:
       - 发送「请你根据以下标准识别：\n并且按照json格式严格输出」+ 结果1 → LLM → 结果4
       - 将 结果4+结果2+结果3 组装生成 程序3 = subfolder/run_program.py
       - 运行 程序3，运行输出保存到 Memory/run_output.txt

用法:
    python3 训练程序.py            # 打开可视化界面
    python3 训练程序.py --selftest # 无界面自检（模拟 LLM，无需 API key）
"""

import argparse
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 提示词模板

CMD0_TEMPLATE = (
    'We need to complete the task "{user_task}". Note: this conversation does NOT complete the task; only plan the workflow.'
    'First make a program design plan (outline only, no code).'
    'First understand two core concepts:'
    '[state = input condition parameter]: the various \'situations/conditions\' of the task; each state can be observed or judged'
    'with a discrete current value (e.g. sunny/rainy, rising/falling, tense/eased). It describes \'what is the situation now\'.'
    '[choice = output action/decision]: the \'operation/decision\' the program makes from the states; an executable final action'
    '(e.g. buy/sell/hold, place/cancel order, approve/reject).'
    'The flow is: state (situation) → final choice (action). Do NOT define any intermediate decision nodes'
    '(no reasoning steps like choice1, choice2, ...); states directly determine the final choice.'
    '[Concept example (ONLY to help you understand state/choice; strictly DO NOT copy its values into this task)]'
    '  Task \'should I bring an umbrella when going out\': state = today\'s weather (sunny/rainy/cloudy), leaving time (morning/noon/evening);'
    '  final choice = bring umbrella / no umbrella. States directly decide the final choice, no intermediate steps.'
    '  In the example, \'weather/time\' are situation parameters (state), \'umbrella/no umbrella\' are actions (choice).'
    '  Note this is only an example: you must derive YOUR OWN states and choices from YOUR task analysis, never reuse this example.'
    'Analyze the current task independently and output a plan:'
    '1) which input states are needed (values of each); 2) the output action choice (final choice values must be directly executable);'
    '3) the state→final choice computation flow (step chain from inputs to final action); 4) how the final choice will be executed (text feedback or script).'
    'Output the plan in clear structured text; planning only, do NOT write any program code.'
)

CMD1_TEMPLATE = (
    'I need to complete the task "{user_task}"; split the work into input states (state) and output actions (choice).'
    '[Concept] state = \'condition parameter\' to observe/judge; each can be judged to a current value from discrete options'
    '(e.g. sunny/rainy, rising/falling, tense/eased); choice = the \'action/decision\' made from the situation,'
    'an executable final action (e.g. approve/reject, place/cancel order).'
    'Independently analyze this task: which \'situations\' need observing (→state), which \'operations\' need deciding (→choice),'
    'and give discrete values for each. Values MUST come from your own analysis; copying examples or templates is forbidden.'
    'Define in strict JSON: at least 1 input state, at least 1 output action choice'
    '(the choices array only defines the final output action choice, no intermediate reasoning steps;'
    'multiple actions may be listed, but each must be directly executable). Each state/choice may have 2+ values.'
    'Output ONLY one JSON object containing all state and choice definitions,'
    'no text, explanation or code fences outside the JSON. JSON format:'
    '{{"need_loop": true or false, '
    '"states": [{{"name": "<state_name>", "values": ["<value1>", "<value2>", ...], '
    '"criteria": "<strict criterion for recognizing the current value of this state>"}}], '
    '"choices": [{{"name": "<choice_name>", "values": ["<value1>", "<value2>", ...], '
    '"role": "final_action", '
    '"criteria": "<strict criterion for determining this choice>"}}]}}'
    'need_loop indicates whether the task needs repeated loops: one-shot tasks set false (avoid waiting in loops);'
    'only continuous monitoring / real-time update tasks set true.'
)

BUILTIN_DATA_URLS = (
    '[Web search mode] All real-time/external data is fetched directly by the LLM via enabled web search (web_search),'
    'no URL declarations, no local fetching.\n'
)


CMD_COLLECT_PLAN = (
    'Based on each state defined in RESULT1, design an information collection plan.'
    '[This task uses the LLM collection path]: all state values are fetched by the LLM and converted to JSON,'
    'do NOT write or run any local collection script.'
    'For each state, explain how the LLM should obtain its current real value:'
    '(e.g. which public webpages/APIs to query, which local file to read, what rule to infer by, or what user info is needed),'
    'and clearly mark which state values depend on user manual input at runtime'
    '(fill in Program2\'s info box, one per line; e.g. account, password, auth codes).'
    '[Real-time/external data] states needing real-time data (weather, quotes, webpages):'
    'the LLM has web search (web_search) enabled; just state "get this real-time data via web search";'
    'no URL declarations, no local webpage fetching.'
    + BUILTIN_DATA_URLS +
    'values unobtainable via web search / common sense must be marked \'user input\'.'
    'Keep the output concise, under 400 words total, as plain text; no JSON.'
)

CMD2 = (
    'Generate a computation program (Python code). At runtime, the state-value JSON is available as constant RESULT4 '
    '(or read from env MAIN_RESULT4; JSON string with only each state\'s current value,'
    'no choice). Write Python code: from RESULT4 state values, directly compute the final output action'
    '(no intermediate nodes like choice1/choice2; states directly decide the final action),'
    'finally output strict JSON decision ({"decision": {"message": "text result"}} or '
    '{"decision": {"script": "real Python code"}}）。'
    '[IMPORTANT] message field: final text conclusion; script field: only real executable Python code,'
    'do NOT put action text (e.g. "buy") into script.'
    '[IMPORTANT] Output ONLY Python program code; no explanation or markdown.'
)

CMD3_EXEC = (
    'Based on the computation flow (RESULT2), write the complete decision executor, merged into ONE complete Python program:\n'
    '1. Write execution logic per the final choice\'s action type:\n'
    '   - Text feedback: print JSON decision {"decision": {"message": "text content"}} (message: text conclusion only)\n'
    '   - Script: execute the script logic and print result, output JSON decision {"decision": {"script": "script code"}} (script: real executable Python only, never action text)\n'
    '2. [AUTO-RUN HARD REQUIREMENT, MUST FOLLOW]\n'
    '   - Auto-run the full flow once and exit; no waiting for user clicks\n'
    '   - Forbidden: input(), messagebox, tkinter windows, mainloop, sleep, loops waiting for user action\n'
    '   - All input data is in constant RESULT4 (JSON string with each state\'s current value);'
    'parse RESULT4 directly; do NOT recollect or ask the user for anything\n'
    '   - Print all output (auto-shown in the Program3 window); the final JSON decision MUST be one complete line\n'
    '   - [MUST MAKE A UNIQUE DECISION] At the end Program3 must print one JSON line: {"decision": {"message": "final decision text"}},'
    'a unique value from the five (or task-defined) actions; [FORBIDDEN] printing only the five options\' descriptions without the final decision;'
    'even with insufficient data / failed fetches, still give a decision (default to a wait-and-see value like "No_Trade" and explain why)\n'
    '   - [PARAM INPUT BOX: for the program itself, not sent to LLM] Program3 window has a built-in param input box;'
    'the AI code MUST declare the input labels at the top:\n'
    '     REQUIRED_INPUTS = ["label1", "label2", ...]   # max 5, plain string list; write [] if none\n'
    '     The window auto-creates inputs by these labels; user fills and clicks \'Submit params\'.\n'
    '     When the program needs them, call window_inputs() to get {"label": "user-entered value"},'
    'and branch logic by them (e.g. query count, filter keyword, items to process).\n'
    '     Login-type tasks (services needing account/password/token) also list credentials as REQUIRED_INPUTS labels,'
    'and use them directly after the user fills them, e.g.:\n'
    '     REQUIRED_INPUTS = ["account", "password"]\n'
    '     ...\n'
    '     p = window_inputs()\n'
    '     client = my_client(p["account"], p["password"])  # implement with stdlib\n'
    '     Params are for the program: never send credentials back to the LLM, never implement your own input UI, no input();'
    'on login failure print the error; do not continue silently\n'
    '   - [OTHER user-selection input] use built-in window_in("prompt_text") (pops an input box in the Program3 window)\n'
    '   - Prefer reading provided environment info from constant ENV_INFO; do not ask again\n'
    '   - In Program3 window output, do NOT use labels like "choice1_result/choice2_result"; use plain step names'
    '(e.g. "Step 1: Collect", "Step 2: Recognize", "Final Decision")\n'
    '3. [FINAL RESULT MUST BE VISIBLE] After finishing, write the final result to Memory/final_result.txt'
    '(create Memory dir first; filename MUST be final_result.txt, fixed, no custom names'
    '(e.g. gold_decision_result.txt, decision_result.txt)], then open the file with the system default program'
    '(Windows: os.startfile, macOS: open, Linux: xdg-open),'
    'so the user can see it directly; no blocking popups;'
    '[IMPORTANT] When opening files, MUST write subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),'
    'the opener subprocess must NOT inherit stdout pipes (or the parent waits forever).\n'
    '4. [ENV INFO] User-provided login/profile info is injected into constant ENV_INFO (JSON object,'
    '(user-provided, may be an empty string). When credentials/account/server address are needed,'
    'read from ENV_INFO; never assume or ask the user again\n'
    '[IMPORTANT] Output ONLY complete Python code (imports, function defs, execution logic); no explanation or markdown.'
)

P3_FRAMEWORK_HINT = (
    '[Program3 run framework (simplified source; your code will be injected into it; write strictly per this framework)]\n'
    'Program3 = built-in run window shell + your code. The shell implements display & inputs; the simplified source below is ready, your code runs inside:\n'
    '```python\n'
    '# Ready constants (use directly; do NOT recollect):\n'
    'RESULT4 = \'{"state1": "value", ...}\'    # state-value JSON string; or read os.environ[\'MAIN_RESULT4\']\n'
    'ENV_INFO = \'{...}\'                      # user env info JSON string (credentials etc., may be empty)\n'
    'COLLECT = RESULT4                         # same as RESULT4\n'
    '# Ready built-in functions (implemented by the shell; call directly):\n'
    'window_out(text)                          # show run status\n'
    'window_in("prompt_text")                       # call when user input is needed; pops input box, returns string\n'
    'window_inputs()                           # render inputs per REQUIRED_INPUTS labels; returns {"label": "value"} dict\n'
    'show_text_result(text) / run_script_with_window(code)   # text/script feedback\n'
    '# batch_util built-in: load_csv_batch / batch_llm_query / batch_loop_run / save_batch_result\n'
    '```\n'
    'Shell behavior: window keeps showing run output; if your code needs no input it runs to the end automatically (window auto-closes);\n'
    'calling window_in/window_inputs shows a prompt in the window and waits for input.\n'
    'Top-level: REQUIRED_INPUTS = ["label1", ...] (max 5; declare when credentials/params needed; write [] and the window only shows status).\n'
    'Example skeleton (your code should be similar):\n'
    '```python\n'
    'import json\n'
    'REQUIRED_INPUTS = []\n'
    'r4 = json.loads(RESULT4) if isinstance(RESULT4, str) else RESULT4\n'
    '# ... execute choice logic per state ...\n'
    'print(json.dumps({"decision": {"message": "Result"}}, ensure_ascii=False))  # final decision on ONE complete line\n'
    '```\n'
)



CMD5 = (
    'Design a set of verification criteria for this task (do NOT actually verify; design only).'
    'The criteria have two parts:\n'
    '1. Process check: is the state→final output flow complete and covering all branches\n'
    '2. Output check: is the final decision (choice) reasonable and aligned with the task goal\n'
    'Keep the output concise, under 400 words total, as plain text; no JSON.'
)

IDENTIFY_PREFIX = 'Recognize by the criteria below and strictly output in JSON format:\n'

SYSTEM_PROMPT = 'You are a rigorous program design assistant. Be accurate and clear; use python code blocks for code.'

# ---------------------------------------------------------------- 可用工具注册表（写入训练 Prompt，告知 LLM 可调用哪些工具）

# 完整工具清单：注入命令0（整体规划）——已精简压缩，避免长请求触发服务端挂起
TOOLS_REGISTRY = (
    '[Available tools (built-in, no install needed; callable directly in code; third-party libraries outside this list are FORBIDDEN)]\n'
    '1. batch_util (built-in):\n'
    '  load_csv_batch(file_path) → read CSV as list[dict];\n'
    '  batch_llm_query(text_list, system_prompt="", batch_size=30) → one batched LLM call returns JSON array (order preserved; main-program env only);\n'
    '  save_batch_result(save_path, results, summary=None, failed=None) → write CSV and return summary JSON text;\n'
    '  batch_loop_run(func, data_list) → loop over items, auto-count failures.\n'
    '2. Program3 built-in window API (auto-provided at Program3 runtime; callable in executor code):\n'
    '  window_out(text) → show text in the Program3 run window;\n'
    '  window_in("prompt_text") → pop an input box for general user input (returns the string);\n'
    '  REQUIRED_INPUTS → the param input labels declared at the top of AI code, e.g. REQUIRED_INPUTS = ["How many?", "Filter keyword"]'
    '(max 5, plain string list; write [] if none). Program3 auto-creates inputs by these labels;\n'
    '  window_inputs() → returns the dict {"label": "user-entered value"} the user filled in the window param inputs'
    '(params for the program itself, e.g. credentials/count/filters; never send credentials to the LLM).\n'
    '3. Reserved extensions (do NOT write code for them, not deployed): cloud Agent (agent_sync_call etc.), screenshot vision (capture_full_screen etc.).\n'
    'Hard constraint: import ONLY built-in batch_util and Python stdlib;'
    'third-party libs forbidden (requests/pandas/pyautogui/selenium etc.).'
)

# 批量任务约束：注入命令3（运算程序）与命令4（执行程序）
BATCH_GUIDANCE = (
    '[Batch-task constraints (if the task processes large batches, e.g. many rows/files, MUST follow)]\n'
    '1. Use built-in batch_util for batch handling: load_csv_batch to read batch input, batch_loop_run to process item by item,'
    'save_batch_result to output the summary;\n'
    '2. Do NOT call the LLM serially per item; for semantic needs use batch_llm_query to batch many items into one JSON array;\n'
    '3. Final output MUST be a single JSON object: {"summary": {"total":..., "category stats":...}, '
    '"results": [{"id":..., "Decision":...}, ...], "failed": [{"item":..., "error":...}]}；'

    '4. Use ONLY built-in batch_util and Python stdlib; no imports outside the list.'
)

# 允许 import 的顶层模块白名单：标准库 + 内置工具
ALLOWED_IMPORTS = {
    "json", "os", "sys", "re", "time", "threading", "subprocess", "csv", "io", "collections",
    "math", "random", "datetime", "urllib", "socket", "base64", "hashlib", "pathlib", "glob",
    "functools", "itertools", "traceback", "contextlib", "typing", "string", "textwrap",
    "sqlite3", "shutil", "tempfile", "platform", "statistics", "decimal", "fractions",
    "dataclasses", "enum", "abc", "copy", "pprint", "warnings", "unicodedata", "codecs",
    "locale", "signal", "queue", "logging", "batch_util", "__future__", "ast",
    # 网络/邮件/解析类（标准库）
    "imaplib", "poplib", "smtplib", "email", "mailbox",
}

# batch_util 内置工具实现源码：注入主程序 / 程序3，无需 pip 安装
BATCH_UTIL_SOURCE = '''
# ============ 内置工具包 batch_util（程序自动注入，无需 pip 安装）============
import csv as _csv
import json as _json
import os as _os
import time as _time

BATCH_LLM_FN = None  # 宿主注入：fn(messages) -> str；未注入时 batch_llm_query 不可用

def load_csv_batch(file_path, encoding="utf-8-sig"):
    """读取 CSV 批量输入，返回 list[dict]（每行一个 dict）。"""
    rows = []
    with open(file_path, "r", encoding=encoding, newline="") as _f:
        for _row in _csv.DictReader(_f):
            rows.append(dict(_row))
    return rows

def batch_llm_query(text_list, system_instruction="", batch_size=30):
    """把 text_list 分批发给 LLM，一次 prompt 返回 JSON 数组（顺序与输入一致）。"""
    global BATCH_LLM_FN
    if BATCH_LLM_FN is None:
        raise RuntimeError(
            "batch_llm_query needs LLM capability, but BATCH_LLM_FN is not injected in this environment"
            "(available in main program only; not in Program3)")
    results = []
    for _i in range(0, len(text_list), batch_size):
        _chunk = text_list[_i:_i + batch_size]
        _msgs = []
        if system_instruction:
            _msgs.append({"role": "system", "content": system_instruction})
        _items = _json.dumps(
            [{"index": _j, "text": _t} for _j, _t in enumerate(_chunk)],
            ensure_ascii=False)
        _msgs.append({"role": "user", "content":
            "Process the following inputs one by one; output ONLY one JSON array (same length & order, each item keeps an index field):\\n" + _items})
        _reply = BATCH_LLM_FN(_msgs)
        _arr = _extract_json_array(_reply)
        results.extend(_arr)
    return results

def _extract_json_array(text):
    try:
        _obj = _json.loads(text)
        if isinstance(_obj, list):
            return _obj
    except Exception:
        pass
    _s = text.find("[")
    if _s != -1:
        _depth = 0
        for _i in range(_s, len(text)):
            if text[_i] == "[":
                _depth += 1
            elif text[_i] == "]":
                _depth -= 1
                if _depth == 0:
                    try:
                        _obj = _json.loads(text[_s:_i + 1])
                        if isinstance(_obj, list):
                            return _obj
                    except Exception:
                        break
    return []

def save_batch_result(save_path, results, summary=None, failed=None):
    """Batch results written to CSV; returns {"summary":..., "results":[...], "failed":[...]} JSON text."""
    _keys = []
    for _r in results:
        for _k in _r.keys():
            if _k not in _keys:
                _keys.append(_k)
    with open(save_path, "w", encoding="utf-8-sig", newline="") as _f:
        if _keys:
            _w = _csv.DictWriter(_f, fieldnames=_keys)
            _w.writeheader()
            _w.writerows(results)
    return _json.dumps(
        {"summary": summary or {}, "results": results, "failed": failed or []},
        ensure_ascii=False)

def batch_loop_run(run_func, data_list, progress_cb=None):
    """Batch loop wrapper: call run_func(item) per item, auto-count failures; returns (success list, failed list)."""
    _ok_rows, _failed_rows = [], []
    _total = len(data_list)
    for _i, _item in enumerate(data_list, 1):
        try:
            _res = run_func(_item)
            _ok_rows.append(_res if isinstance(_res, dict) else {"result": _res})
        except Exception as _e:
            _failed_rows.append({"item": _item, "error": str(_e)})
        if progress_cb:
            try:
                progress_cb(_i, _total)
            except Exception:
                pass
    return _ok_rows, _failed_rows



# ============ batch_util 结束 ============
'''


def validate_code_imports(code, label):
    """校验代码 import 的库是否在白名单内，并做语法检查。
    返回 (ok: bool, problems: list[str])。"""
    problems = []
    for _m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M):
        _top = _m.group(1).split(".")[0]
        if _top not in ALLOWED_IMPORTS:
            problems.append(
                f"{label}: used a library outside the tool list: {_m.group(1)}"
                f"(only built-in batch_util and stdlib allowed; third-party libs forbidden)")
    try:
        compile(code, "<check>", "exec")
    except SyntaxError as _e:
        problems.append(f"{label}: syntax error {_e}")
    return (not problems), problems

# ---------------------------------------------------------------- 配置

# 全局停止请求（程序1 GUI「停止」按钮使用；训练每步之间检查）
STOP_REQUESTED = [False]

# 所有输出都基于程序文件所在目录（不依赖运行时的工作目录，
# 避免 Windows 下以管理员/其他方式运行时被写入 C:\Windows\system32 而拒绝访问）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",   # 火山方舟/豆包；OpenAI 用 https://api.openai.com/v1
    "api_key": os.environ.get("ARK_API_KEY") or os.environ.get("DOUBAO_API_KEY") or "",
    "model": "doubao-seed-2-0-lite-260428",                   # 默认豆包 2.0 lite；可在界面下拉选择或手动改
    "temperature": 0.2,
    "timeout": 280,       # 单次 LLM 调用超时（秒）
    "run_timeout": 1800,  # 运行生成的程序超时（秒）
    "web_search": False,  # 火山方舟联网内容插件：需模型支持（Doubao 系列/DeepSeek R1），开启后 LLM 可自行搜索
    "max_fix_rounds": 5,  # 自动迭代修复最大轮次（训练时验证代码，失败自动让 LLM 重写直到跑通）
}

# 已知过时/已下线/格式不可靠的模型名：旧配置里残留时自动回退到默认模型，避免实际调用 404
OBSOLETE_MODELS = {
    "doubao-seed-1-6-250615",
    "doubao-1-5-pro-32k-250115",
    "doubao-seed-2-1-pro-260628",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"WARN: failed to read config.json ({e}); using default config", file=sys.stderr)
    # 安全策略：API Key 永不从配置文件回填，只来自环境变量（打开程序时不会自动带出）
    cfg["api_key"] = DEFAULT_CONFIG["api_key"]
    # 兼容旧配置：config.json 里残留的旧/已下线模型名自动回退到新默认
    if cfg.get("model") in OBSOLETE_MODELS or not cfg.get("model"):
        cfg["model"] = DEFAULT_CONFIG["model"]
    cfg["model"] = normalize_model_name(cfg.get("model", ""), cfg.get("base_url", ""))
    return cfg


def save_config(cfg):
    # 只保存非敏感配置，API Key 不写入配置文件
    data = {k: v for k, v in cfg.items() if k != "api_key"}
    with open(CONFIG_PATH, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- LLM 调用

# 平台预设：选择平台后自动填入正确的接口地址和模型示例
PLATFORM_PRESETS = {
    "Volcano Ark / Doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-lite-260428",
        "note": "key=console APIKey (UUID style); model=console model ID (e.g. doubao-seed-2-0-lite-260428) or endpoint ID (ep-...), copied from 'Manage'",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "Key starts with sk-",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "Key starts with sk-",
    },
    "Tongyi Qianwen (Alibaba Bailian)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "Key starts with sk-",
    },
    "Zhipu GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "note": "Key starts with digits.digits (e.g. 123.abc...)",
    },
    "Kimi(Moonshot)": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "note": "Key starts with sk-",
    },
}

# 各平台常用模型名（下拉框随平台预设联动；均可手动输入任意模型 ID）
MODELS_BY_PLATFORM = {
    "Volcano Ark / Doubao": [
        "doubao-seed-2-0-lite-260428",
        "doubao-seed-2-0-mini-260428",
    ],
    "OpenAI": ["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini", "o3-mini"],
    "DeepSeek": ["deepseek-chat", "deepseek-reasoner"],
    "Tongyi Qianwen (Alibaba Bailian)": ["qwen-plus", "qwen-max", "qwen-turbo"],
    "Zhipu GLM": ["glm-4-flash", "glm-4-plus", "glm-4-air"],
    "Kimi(Moonshot)": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
}

# 兼容旧版：仅用于读取旧配置时的模型名升级
COMMON_MODELS = [m for lst in MODELS_BY_PLATFORM.values() for m in lst]


def _http_hint(code, base, model):
    """Map an HTTP status code to a troubleshooting suggestion."""
    if code in (401, 403):
        return ("Auth failed: 1) is the API Key complete (no leading/trailing spaces)? 2) does the Key match the selected platform"
                "(Volcano Ark Keys cannot be used on OpenAI/DeepSeek endpoints)? 3) for Volcano Ark use the console-created "
                "APIKey (UUID format), and enable the model/endpoint in the console.")
    if code == 404:
        return (f"Wrong address or model name: confirm the API path is complete and model {model!r} is available for your account."
                "Volcano Ark note: copy the lowercase model ID from console 'Manage' (e.g. doubao-seed-2-0-lite-260428)"
                "or endpoint ID (ep-...); do NOT copy the display name (e.g. Doubao-Seed-2.0-lite 260428),"
                "and do not select an unenabled model.")
    if code == 429:
        return "Too many requests or insufficient quota: retry later, or check balance/rate limits."
    if code >= 500:
        return "Platform server temporarily down: retry later."
    return ""


def normalize_model_name(model, base_url=""):
    """把用户手动输入的模型名规范化为平台能识别的格式。
    火山方舟：控制台显示名（如 Doubao-Seed-2.0-lite 260428）→ API 模型 ID
    （doubao-seed-2-0-lite-260428）：空格压缩为连字符、小写、点转连字符；推理接入点 ID（ep- 开头）只去空格。
    其他平台：只去首尾空白。"""
    m = (model or "").strip()
    if not m:
        return m
    base = (base_url or "").lower()
    if "volces.com" in base or "ark" in base:
        if m.startswith("ep-"):
            return m.replace(" ", "")
        return re.sub(r"\s+", "-", m).replace(".", "-").lower()
    return m


def _diag_network(url, connect_timeout=15):
    """Quickly diagnose DNS resolution and TCP connectivity to distinguish network vs API issues."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        t0 = time.time()
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        dns_t = time.time() - t0
        addrs = sorted({i[4][0] for i in infos[:5]})
        t0 = time.time()
        sock = socket.create_connection((host, port), timeout=connect_timeout)
        sock.close()
        tcp_t = time.time() - t0
        return (f"DNS resolve {host} → {addrs} ({dns_t:.2f}s);"
                f"TCP connect {host}:{port} OK ({tcp_t:.2f}s). Network layer reachable,"
                f"timeout happens while waiting for the LLM server to return data.")
    except Exception as e:
        return f"Network diag: {e} (an error here means DNS/TCP connectivity itself is broken)"


# ---------------------------------------------------------------- LLM 对话记录
# 所有 LLM 调用（发送+接收）统一记录，训练结束/程序运行后写入「Memory/llm_chat_log.txt」
LLM_DIALOG_LOG = []        # 模块级：[{send, receive, ok, mock}]
CURRENT_MEM_DIR = [None]   # 当前任务Memory目录：训练程序运行时设置，自动写盘对话记录


def flush_dialog_log(mem_dir, emit=print):
    """把 LLM_DIALOG_LOG 全量写入 Memory/llm_chat_log.txt。"""
    if not mem_dir:
        return
    try:
        os.makedirs(mem_dir, exist_ok=True)
        parts = []
        for i, d in enumerate(LLM_DIALOG_LOG, 1):
            parts.append(
                "[Dialogue %d%s]\n[SEND]\n%s\n[RECV]\n%s" % (
                    i, "(mock)" if d.get("mock") else "",
                    json.dumps(d.get("send", []), ensure_ascii=False, indent=1),
                    d.get("receive", "")))
        save_text(os.path.join(mem_dir, "llm_chat_log.txt"), "\n\n".join(parts))
    except Exception as e:
        emit(f"[LOG] Failed to write LLM chat log: {e}")


def call_llm(cfg, messages):
    """调用 OpenAI 兼容的 chat/completions 接口，返回回复文本。"""
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    if not base.startswith("http://") and not base.startswith("https://"):
        raise RuntimeError(f"Invalid API base: {base!r} (must start with http:// or https://)")
    if not key:
        raise RuntimeError("API Key is empty: paste it in the GUI config area,"
                           "or set the ARK_API_KEY / DOUBAO_API_KEY env var")
    model = normalize_model_name(cfg.get("model", ""), base)
    _web = bool(cfg.get("web_search"))
    if _web:
        # 联网模式：方舟联网内容插件 web_search 仅支持 Responses API（/responses），chat/completions 会 400
        url = base + "/responses"
        _last_user = None
        for _m in reversed(messages):
            if _m.get("role") == "user":
                _last_user = _m
                break
        if _last_user is None:
            _last_user = messages[-1] if messages else {"role": "user", "content": ""}
        payload = {
            "model": model,
            "input": [{"role": _last_user.get("role", "user"),
                        "content": [{"type": "input_text", "text": str(_last_user.get("content", ""))}]}],
            "tools": [{"type": "web_search"}],
            "stream": False,
        }
    else:
        url = base + "/chat/completions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        # 直连：绕过系统代理（系统代理会在空闲 30s 左右掐断长请求，导致 flash 模型长文本无响应）
        _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with _opener.open(req, timeout=cfg.get("timeout", 280)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:600]
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[HTTP error {e.code}] {detail}", "ok": False})
        hint = _http_hint(e.code, base, model)
        raise RuntimeError(
            f"LLM API error {e.code}\n"
            f"Request URL: {url}\n"
            f"Platform response: {detail}\n"
            + (("Troubleshooting: " + hint) if hint else ""))
    except Exception as e:
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[ERROR] {e}", "ok": False})
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            diag = _diag_network(url)
            prox = [p for p in ("http_proxy", "https_proxy", "all_proxy")
                    if os.environ.get(p) or os.environ.get(p.upper())]
            prox_txt = ("System proxy env detected: " + ", ".join(prox) +
                        " → if curl also times out, temporarily disable the proxy/VPN and retry." if prox
                        else "No system proxy env detected.")
            curl_body = json.dumps({"model": model, "messages": messages,
                                    "temperature": cfg.get("temperature", 0.2)},
                                   ensure_ascii=False)[:300]
            raise RuntimeError(
                f"LLM request timeout (waited {cfg.get('timeout', 280)}s): prompt too long or network slow.\n"
                f"Request URL: {url}\nRaw error: {e}\n{diag}\n{prox_txt}\n"
                f"Self-test command (run in terminal; replace <your API Key> to check whether it is an API issue):\n"
                f"  curl -sS -m 60 {url} \\\n"
                f"    -H 'Authorization: Bearer <your API Key>' -H 'Content-Type: application/json' \\\n"
                f"    -d '{curl_body}'\n"
                f"Judge: 1) curl also times out → network/proxy issue; 2) curl quickly returns 401/404 → Key or model-name issue"
                f"(see the 'Test connection' button's suggestions); 3) curl returns fine → raise 'Call timeout' to 600s and retry.")
        raise RuntimeError(f"Cannot connect to LLM API ({base}); check network or address: {e}")
    try:
        if _web:
            _txts = []
            for _o in (body.get("output") or []):
                if _o.get("type") == "message":
                    for _c in (_o.get("content") or []):
                        if _c.get("type") == "output_text":
                            _txts.append(_c.get("text", ""))
            content = "\n".join(_txts)
            if not content:
                raise KeyError("output_message_empty")
        else:
            content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Unexpected LLM reply format: {str(body)[:300]}")
    LLM_DIALOG_LOG.append({"send": messages, "receive": content, "ok": True})
    return content


# ---------------------------------------------------------------- 自检（模拟 LLM）

MOCK_RESULTS = {
    0: ('Plan: 1) capture 10 gold-related webpages, extract 5 condition parameters (state1 Gold_Price, state2 24h_Change,'
        'state3 USD_Index, state4 Risk_Sentiment, state5 ETF_Flow);'
        '2) final decision action (Buy_Full/Sell_Full/Buy_Half/Sell_Half/No_Trade);'
        '3) synthesize the 5 parameters into the final decision; 4) text feedback.'),
    1: ('{"states": [{"name": "state1", "values": ["Rising", "Falling", "Sideways"], '
        '"criteria": "recognize state1 value by the trend of international spot gold price"}, '
        '{"name": "state2", "values": ["Strong", "Weak", "Neutral"], '
        '"criteria": "recognize state2 value by the strength of 24h gold price change"}, '
        '{"name": "state3", "values": ["Strengthening", "Weakening", "Sideways"], '
        '"criteria": "recognize state3 value by USD index trend"}, '
        '{"name": "state4", "values": ["High", "Medium", "Low"], '
        '"criteria": "recognize state4 value by global risk sentiment / risk events"}, '
        '{"name": "state5", "values": ["Net_Inflow", "Net_Outflow", "Flat"], '
        '"criteria": "recognize state5 value by gold ETF net inflow direction"}], '
        '"choices": [{"name": "Gold_Trade_Action", '
        '"values": ["Buy_Full", "Sell_Full", "Buy_Half", "Sell_Half", "No_Trade"], '
        '"role": "final_action", "criteria": "decide buy/sell by synthesizing the 5 indicators"}]}'),
    2: ('```python\n'
        'import json\n'
        'r4 = json.loads(RESULT4) if isinstance(RESULT4, str) else RESULT4\n'
        'print("RESULT2 (compute flow) reads state:", r4.get("state1"), r4.get("state2"), r4.get("state3"), r4.get("state4"), r4.get("state5"))\n'
        's1 = r4.get("state1"); s4 = r4.get("state4"); s5 = r4.get("state5")\n'
        'if s4 == "High" and s1 == "Rising":\n'
        '    decision = "Buy_Full"\n'
        'elif s1 == "Falling" and s5 == "Net_Outflow":\n'
        '    decision = "Sell_Full"\n'
        'elif s4 == "Medium" and s1 == "Rising":\n'
        '    decision = "Buy_Half"\n'
        'elif s1 == "Falling":\n'
        '    decision = "Sell_Half"\n'
        'else:\n'
        '    decision = "No_Trade"\n'
        'print(json.dumps({"decision": {"message": decision}}, ensure_ascii=False))'),
    3: ('```python\n'
        'import json\n'
        'print("RESULT3 (executor) executes feedback per decision")\n'
        'show_text_result("Final decision executed")'),
    4: ('```python\n'
        'def show_text_result(msg):\n'
        '    print("[Window] ", msg)\n'
        'def run_script_with_window(code, title="Running script"):\n'
        '    exec(compile(code, "<script>", "exec"))'),
    5: '{"indicators": ["each state/choice definition consistent with its criteria"], '
       '"self_checks": [{"indicator": "definition consistent with criteria", "passed": true, "note": "passed"}], '
       '"conclusion": "validation passed (mock)"}',
    6: 'OK: no changes needed',
    "collect_plan": ('{"states_collect": ['
                     '{"state": "state1", "method": "prompt", "source": "International spot gold price (USD/oz)", "script": null}, '
                     '{"state": "state2", "method": "prompt", "source": "Gold 24h change", "script": null}, '
                     '{"state": "state3", "method": "prompt", "source": "USD index trend", "script": null}, '
                     '{"state": "state4", "method": "prompt", "source": "Global risk sentiment / risk events", "script": null}, '
                     '{"state": "state5", "method": "prompt", "source": "Gold_ETF_Flow", "script": null}], '
                     '"note": "all 5 indicators need real-time gold data via web search (web_search), capturing 10 webpages"}'),
    "feedback": ('[MODIFY RESULT3]\n'
                 '```python\n'
                 'import json\n'
                 'print("RESULT3 (feedback-fixed) executes feedback per decision")\n'
                 'show_text_result("Final decision executed")\n'
                 '```'),
    "new_chat": 'OK: new conversation started (mock).',
    "fix_code": ('```python\n'
                 'import json\n'
                 'r4 = json.loads(RESULT4) if isinstance(RESULT4, str) else RESULT4\n'
                 'print("RESULT2 (fixed) reads state:", r4.get("state1"), r4.get("state2"), r4.get("state3"), r4.get("state4"), r4.get("state5"))\n'
                 'decision = "Buy_Full" if r4.get("state4") == "High" and r4.get("state1") == "Rising" else "No_Trade"\n'
                 'print(json.dumps({"decision": {"message": decision}}, ensure_ascii=False))\n'
                 '```\n'
                 '```python\n'
                 'import json\n'
                 'print("RESULT3 (fixed) executes feedback")\n'
                 'show_text_result("Final decision executed")\n'
                 '```'),
    "fix_runtime": ('```python\n'
                    'import json\n'
                    'print("RESULT3 (runtime-fixed) executes feedback")\n'
                    'show_text_result("Final decision executed")\n'
                    '```'),
}

# 模拟：转换步骤输出的 state 当前取值 JSON（只含 state，不含 choice）
MOCK_RESULT4 = '{"state1": "Rising", "state2": "Strong", "state3": "Weakening", "state4": "High", "state5": "Net_Inflow"}'

MOCK_CONNECTION_REPLY = '__CONNECTION_OK__ LLM connected (mock).'


def ask_llm(cfg, messages, mock=False, mock_key=None):
    if mock:
        r = MOCK_RESULTS[mock_key]
        LLM_DIALOG_LOG.append({"send": messages, "receive": r, "ok": True, "mock": True})
    else:
        r = call_llm(cfg, messages)  # call_llm 内已记录
    if CURRENT_MEM_DIR[0]:
        flush_dialog_log(CURRENT_MEM_DIR[0])
    return r


def test_llm_connection(cfg, emit=print, mock=False):
    """Really send a marked test message to the LLM; success only when the reply contains the mark."""
    def _mask_key(k):
        k = (k or "").strip()
        return (k[:4] + "****" + k[-4:]) if len(k) > 8 else ("*" * len(k))
    # 测试连接用短超时（最多60秒）：快速失败，避免服务器hang时用户干等180秒"No response"
    test_cfg = dict(cfg)
    test_cfg["timeout"] = 60
    emit(f"[CONN TEST] Sending real test message → {cfg.get('base_url')}/chat/completions | "
         f"Model: {normalize_model_name(cfg.get('model'), cfg.get('base_url'))} | Key: {_mask_key(cfg.get('api_key'))} ...")
    emit("[CONN TEST] Waiting for LLM response (up to 60s)...")
    if mock:
        reply = MOCK_CONNECTION_REPLY
        emit("[CONN TEST] (mock) returning mock reply")
    else:
        try:
            reply = call_llm(test_cfg, [{"role": "user",
                                        "content": "1"}])
        except Exception as e:
            emit(f"[CONN TEST] Failed: {e}")
            emit("[CONN TEST] Troubleshooting:")
            emit("  1. If timeout: pro models are reasoning models and respond slowly; raise 'Call timeout' to 300s")
            emit("  2. If 401/auth failed: check the API Key is complete with no leading/trailing spaces")
            emit("  3. If 404/model missing: confirm the model is enabled in the Volcano Ark console, or copy an endpoint ID (ep-...)")
            raise
    emit("[CONN TEST] LLM raw reply:")
    for line in (reply or "").splitlines()[:20]:
        emit("  | " + line)
    if reply and reply.strip():
        emit("[CONN TEST] Success: LLM returned content; connection OK.")
    else:
        emit("[CONN TEST] WARN: LLM returned empty.")
    rec = os.path.join(BASE_DIR, "llm_test_log.txt")
    save_text(rec, reply)
    emit(f"[CONN TEST] Reply saved: {rec}")
    return reply


# ---------------------------------------------------------------- 工具函数

def summary(text, n=200):
    t = (text or "").replace("\n", " ").strip()
    return t[:n] + ("..." if len(t) > n else "")


def decode_bytes(b):
    """Decode subprocess output via utf-8 → gbk → latin-1 in turn to avoid garbled text on Windows."""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    return b.decode("utf-8", "replace")


def save_text(path, text):
    # utf-8-sig 带 BOM，Windows 记事本直接打开不乱码
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text or "")


def read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except FileNotFoundError:
        return default
    except Exception:
        return default


def safe_task_name(task, max_len=40):
    name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", task).strip("_")
    return name[:max_len] or "task"


def strip_code_fence(text):
    text = (text or "").strip()
    m = re.search(r"```[a-zA-Z]*\s*\n?(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    return text


def flatten_state_json(parsed):
    """Compatible nested wrapper: {"need_loop":..., "states": {"name": "value"} → flat {"name": "value"}。"""
    if not isinstance(parsed, dict):
        return parsed
    _st = parsed.get("states")
    if isinstance(_st, dict):
        parsed = dict(_st)
    elif isinstance(_st, list):
        _flat = {}
        for _item in _st:
            if isinstance(_item, dict) and _item.get("name"):
                _flat[_item["name"]] = (_item.get("value") or _item.get("value")
                                        or (_item.get("values") or [""])[0] if isinstance(_item.get("values"), list) else _item.get("values"))
        if _flat:
            parsed = _flat
    parsed.pop("need_loop", None)
    return parsed


def extract_json(text):
    """从文本中提取第一个 JSON 对象/数组。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    for a, b in (("{", "}"), ("[", "]")):
        start = text.find(a)
        while start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == a:
                    depth += 1
                elif text[i] == b:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except Exception:
                            break
            start = text.find(a, start + 1)
    return None


def run_program3_local(cfg, root_dir, result4, emit=print):
    """Re-run Program3 from Program1 side (for re-run after feedback fix): capture output and save to Memory/run_output.txt."""
    sub_dir = os.path.join(root_dir, "subfolder")
    mem_dir = os.path.join(root_dir, "Memory")
    py3 = os.path.join(sub_dir, "run_program.py")
    if not os.path.exists(py3):
        emit("[FEEDBACK-FIX] Program3 not found; train first")
        return -1
    env = dict(os.environ)
    env["MAIN_RESULT4"] = json.dumps(flatten_state_json(extract_json(result4) or {}), ensure_ascii=False)
    env["P3_NO_GUI"] = "1"   # 静默模式：不弹窗口，输出走控制台
    emit("[FEEDBACK-FIX] Re-running Program3...")
    try:
        proc = subprocess.Popen([sys.executable, py3], cwd=sub_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        # 轮询等待退出（LLM 生成的程序3 可能启动 xdg-open 等子进程继承管道导致 communicate 永久阻塞）
        try:
            _wait_n = 0
            while proc.poll() is None and _wait_n < cfg.get("run_timeout", 280):
                time.sleep(0.2); _wait_n += 0.2
            if proc.poll() is None:
                proc.kill()
                emit("[FEEDBACK-FIX] Program3 timeout; terminated")
            out_b, err_b = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_b, err_b = proc.communicate()
        out = (decode_bytes(out_b) or "") + \
              ("\n" + decode_bytes(err_b) if err_b else "")
        save_text(os.path.join(mem_dir, "run_output.txt"), out)
        emit("[FEEDBACK-FIX] Program3 exit code: " + str(proc.returncode))
        emit(out[-3000:] if out.strip() else "(no output)")
        return proc.returncode
    except Exception as e:
        emit(f"[FEEDBACK-FIX] Failed to run Program3: {e}")
        return -1


def _parse_feedback_parts(reply):
    """从 LLM 反馈回复中解析「段位标签」修改指令（容错：标签可带冒号/解释文字/顺序任意）。
    判定依据：每个代码块之前的文本（前 200 字符）内是否出现 结果2/结果3（或 result2/result3），
    支持【修改结果2】、【修改结果3：】、结果3:、无括号等变体，也兼容 JSON 指令
    {"result2": "...", "result3": "..."}. Return {"result2": code, "result3": code} (only the modified parts)."""
    parts = {}
    for _m in re.finditer(r"```[a-zA-Z]*\s*\n(.*?)```", reply, re.S):
        _code = _m.group(1).strip()
        if not _code:
            continue
        _head = reply[max(0, _m.start() - 200):_m.start()]
        _tags = list(re.finditer(r"RESULT\s*([23])|result\s*([23])", _head, re.I))
        if _tags:  # 取离代码块最近的标签（最后一个），避免被前一个标签干扰
            _g = _tags[-1].group(1) or _tags[-1].group(2)
            parts.setdefault("result3" if _g == "3" else "result2", _code)
    if not parts:
        try:
            j = extract_json(reply) or {}
            for key in ("result2", "result3"):
                v = j.get(key)
                if v and str(v).strip():
                    parts[key] = str(v).strip()
        except Exception:
            pass
    return parts


def feedback_fix(cfg, question, emit=print, mock=False):
    """【手动反馈修正】用户输入问题 → 把 程序3 的 AI 逻辑（结果2/结果3）+ 运行输出 + 用户问题 发给 LLM →
    LLM 按「段位标签」声明修改后的代码粘贴到哪一段（【修改结果2】/【修改结果3】）→
    程序只替换对应段，系统框架（batch_util/窗口壳/采集/常量）完全不动 → 重新运行。"""
    cur = os.path.join(BASE_DIR, "task_output", "current_task.txt")
    if not os.path.exists(cur):
        emit("[FEEDBACK-FIX] No task trained yet; train first")
        return
    with open(cur, "r", encoding="utf-8-sig") as f:
        root_dir = f.read().strip()
    sub_dir = os.path.join(root_dir, "subfolder")
    mem_dir = os.path.join(root_dir, "Memory")
    py3 = os.path.join(sub_dir, "run_program.py")
    if not os.path.exists(py3):
        emit("[FEEDBACK-FIX] Program3 (subfolder/run_program.py) not found; train and run first")
        return

    def read(name):
        p = os.path.join(mem_dir, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                return f.read()
        return ""

    task = read("task_desc.txt") or os.path.basename(root_dir)
    r1 = read("result1.txt")
    r6 = read("result6.txt")
    r4 = read("state_values.txt")
    out = read("run_output.txt")
    cur_r2 = read("result2.txt")
    cur_r3 = read("result3.txt")
    with open(py3, "r", encoding="utf-8-sig") as f:
        code = f.read()

    prompt = (
        "You are the AI-logic fixer of Program3 (an auto-generated run program). Program3 consists of the [system framework (NOT modifiable)]"
        "(built-in batch_util, info constants, window shell) and [AI logic (modifiable)].\n"
        "AI logic has only two parts: RESULT2 (state→choice compute flow) and RESULT3 (choice executor)."
        "You may modify ONLY these two parts, and MUST declare which part the new code goes into.\n\n"
        f"Task: {task}\n\n"
        f"RESULT1 (state/choice definitions & criteria): {r1}\n\n"
        f"RESULT6 (verification criteria): {r6}\n\n"
        f"State-value JSON (identified result, states only): {r4}\n\n"
        "Current AI logic code (for your reference):\n"
        f"[RESULT2 current code]\n{cur_r2 or "(none)"}\n\n"
        f"[RESULT3 current code]\n{cur_r3 or "(none)"}\n\n"
        "Program3 recent run output:\n" + (out[-2000:] if out else "(no output)") + "\n\n"
        f"User feedback question: {question}\n\n"
        "Output ONLY the parts you modify (one or both), strictly in this format:\n"
        "[MODIFY RESULT2]\n```python\n<new RESULT2 full code>\n```\n"
        "[MODIFY RESULT3]\n```python\n<new RESULT3 full code>\n```\n"
        "No framework code, no explanation, nothing else."
    )
    emit("[FEEDBACK-FIX] Sending AI logic (RESULT2/3) + run output + user question to LLM...")
    if mock:
        reply = MOCK_RESULTS["feedback"]
        emit("[FEEDBACK-FIX] (mock) returning mock result")
    else:
        reply = call_llm(cfg, [{"role": "user", "content": prompt}])
    save_text(os.path.join(mem_dir, "feedback_output.txt"), reply)
    flush_dialog_log(mem_dir, emit)  # 反馈对话也写入 llm_chat_log.txt

    # 解析段位标签：明确 LLM 修改的是哪一段、贴到哪
    parts = _parse_feedback_parts(reply)
    if not parts:
        emit("[FEEDBACK-FIX] Could not parse the [MODIFY RESULT2/3] section tags from the LLM reply; Program3 unchanged")
        emit(f"[FEEDBACK-FIX] LLM raw reply: {summary(reply, 300)}")
        return
    final_r2 = parts.get("result2", cur_r2 or "")
    final_r3 = parts.get("result3", cur_r3 or "")
    # 静态校验：语法 + import 白名单（框架段不动，AI 段也要先过关）
    for label, c in (("RESULT2", final_r2), ("RESULT3", final_r3)):
        if not c.strip():
            continue
        ok, errs = validate_code_imports(strip_code_fence(c), label)
        if not ok:
            emit(f"[FEEDBACK-FIX] {label} failed validation (syntax or whitelist); not replaced: " + "; ".join(errs))
            return
    # 只替换 AI 逻辑段（RESULT2/RESULT3/_BLOCKS 三行），框架段完全不动
    new_code = code
    if "result2" in parts:
        new_code = re.sub(r"^RESULT2 = .*$", lambda m: "RESULT2 = " + repr(final_r2), new_code, count=1, flags=re.M)
    if "result3" in parts:
        new_code = re.sub(r"^RESULT3 = .*$", lambda m: "RESULT3 = " + repr(final_r3), new_code, count=1, flags=re.M)
    _blk = strip_code_fence(final_r2) + "\n\n# ============ module separator ============\n\n" + strip_code_fence(final_r3)
    new_code = re.sub(r"^_BLOCKS = .*$", lambda m: "_BLOCKS = " + repr(_blk), new_code, count=1, flags=re.M)
    # 同步更新Memory与主程序.py 里的结果2/结果3（程序2 重跑时保持一致）
    if "result2" in parts:
        save_text(os.path.join(mem_dir, "result2.txt"), final_r2)
    if "result3" in parts:
        save_text(os.path.join(mem_dir, "result3.txt"), final_r3)
    mp3 = os.path.join(root_dir, "main_program.py")
    if os.path.exists(mp3):
        try:
            with open(mp3, "r", encoding="utf-8-sig") as f:
                msrc = f.read()
            if "result2" in parts:
                msrc = re.sub(r"^RESULT2 = .*$", lambda m: "RESULT2 = " + repr(final_r2), msrc, count=1, flags=re.M)
            if "result3" in parts:
                msrc = re.sub(r"^RESULT3 = .*$", lambda m: "RESULT3 = " + repr(final_r3), msrc, count=1, flags=re.M)
            with open(mp3, "w", encoding="utf-8-sig") as f:
                f.write(msrc)
        except Exception as _e:
            emit(f"[FEEDBACK-FIX] Syncing main_program.py failed (does not affect this Program3 fix): {_e}")
    emit("[FEEDBACK-FIX] AI logic replaced per section (framework untouched); restarting Program3...")
    save_text(py3, new_code)
    run_program3_local(cfg, root_dir, r4, emit=emit)
    emit("[DONE] feedback fix complete; Program3 re-run")


# ---------------------------------------------------------------- 程序2 模板（主程序.py）

# ================================================================
# 程序3（run_program.py）代码契约 —— 系统生成时写入程序3 头部，请勿手改
# 【不可更改 · 系统框架】（由程序2 生成，运行受窗口壳保护）
#   段1 头部/import · 段2 内置工具 batch_util · 段3 信息常量
#   段4 输出辅助 · 段5 本地脚本采集 · 段7 窗口壳
# 【可更改 · AI 逻辑】（每次任务由 LLM 重新生成）
#   段6 _BLOCKS = 结果2(state→choice 运算流程) + 结果3(choice 执行程序)
# AI 代码可用：window_out() window_in() window_inputs()
#   show_text_result() run_script_with_window() RESULT4 COLLECT
#   ENV_INFO 与内置 batch_util 工具
# 禁止：白名单外 import、阻塞式输入等待（静默模式已自动返回空串）、
#   sys.exit()、长时间死循环、直接修改框架段变量
# ================================================================
P3_CONTRACT_TEXT = '''# Program3 code contract (system-generated; do NOT hand-edit)
# [IMMUTABLE] Part1 header/import · Part2 batch_util · Part3 info constants · Part4 output helper · Part5 local collection · Part7 window shell — system framework
# 【可更改】段6 _BLOCKS（结果2 state→choice 运算流程 ＋ 结果3 choice 执行程序）—— 每次任务由 LLM 重新生成
# AI 代码可用：window_out / window_in / window_inputs / show_text_result / run_script_with_window / RESULT4 / COLLECT / ENV_INFO / batch_util
# 禁止：白名单外 import、阻塞式输入等待、sys.exit()、死循环、修改框架段变量'''

MAIN_PROGRAM_TEMPLATE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主程序.py —— 程序2（由 训练程序.py 自动生成，带 LLM 连接可视化界面）
Task: @@TASK@@

功能:
  - 可视化界面: 输入 API 地址 / 密钥 / 模型
  - 三个按钮：「采集 LLM 信息并运算」「采集本地信息并运算」「向 LLM 输入信息（人工调试）」
  - 将 结果4+结果2+结果3 组装生成 程序3 = subfolder/run_program.py 并运行

用法:
    python3 主程序.py            # 打开可视化界面（LLM 配置 + 测试连接 + 执行）
    python3 主程序.py --auto     # 命令行自动执行（训练程序调用本模式）
"""

import argparse
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# ---- 程序1 训练结果与配置（由训练程序写入，完整保留） ----
CONFIG = @@CONFIG@@
RESULT0 = @@RESULT0@@
RESULT1 = @@RESULT1@@
COLLECT_PLAN = @@COLLECT_PLAN@@
RESULT2 = @@RESULT2@@
RESULT3 = @@RESULT3@@
WINDOW_CODE = @@RESULT4W@@
RESULT6 = @@RESULT6@@
TASK = @@TASK_REPR@@

# ---- 内置工具 batch_util（由训练程序注入，无需 pip 安装）----
BATCH_UTIL_SOURCE = @@BATCH_UTIL@@
P3_CONTRACT_TEXT = @@P3_CONTRACT@@
BUILTIN_DATA_URLS = @@BUILTIN_URLS@@
exec(compile(BATCH_UTIL_SOURCE, "<batch_util>", "exec"))
# 主程序运行环境：给 batch_util 绑定 LLM 批量理解能力
BATCH_LLM_FN = lambda _msgs: call_llm(_msgs, CONFIG)

IDENTIFY_PREFIX = 'Recognize by the criteria below and strictly output in JSON format:\n'

# 当前运行中的子进程（程序3 / 收集脚本），供「停止」按钮终止
CURRENT_PROC = [None]

# 停止请求标志：「停止」按钮置 True；run_auto 各阶段检查后提前退出
STOP_REQUESTED = [False]

MOCK_REPLY = @@MOCK4@@
MOCK_COLLECT = ('Raw collected info (via web search):\n'
    'Gold price: ~2650 USD/oz (web search: kitco/goldprice real-time quote)\n'
    '24h change: roughly flat (web search: gold real-time quotes)\n'
    'USD index: roughly flat (web search: USD index quotes)\n'
    'Risk sentiment: neutral (web search: geopolitics & market risk news)\n'
    'Gold ETF net inflow: zero or missing data (web search: gold ETF holdings)')
MOCK_CONNECTION_REPLY = '__CONNECTION_OK__ LLM connected (mock).'


PLATFORM_PRESETS = {
    "Volcano Ark / Doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-lite-260428",
        "note": "key=console APIKey (UUID style); model=console model ID (e.g. doubao-seed-2-0-lite-260428) or endpoint ID (ep-...), copied from 'Manage'",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "Key starts with sk-",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "Key starts with sk-",
    },
    "Tongyi Qianwen (Alibaba Bailian)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "Key starts with sk-",
    },
    "Zhipu GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "note": "Key starts with digits.digits (e.g. 123.abc...)",
    },
    "Kimi(Moonshot)": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "note": "Key starts with sk-",
    },
}

# 各平台常用模型名（下拉框随平台预设联动；均可手动输入任意模型 ID）
MODELS_BY_PLATFORM = {
    "Volcano Ark / Doubao": [
        "doubao-seed-2-0-lite-260428",
        "doubao-seed-2-0-mini-260428",
    ],
    "OpenAI": ["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini", "o3-mini"],
    "DeepSeek": ["deepseek-chat", "deepseek-reasoner"],
    "Tongyi Qianwen (Alibaba Bailian)": ["qwen-plus", "qwen-max", "qwen-turbo"],
    "Zhipu GLM": ["glm-4-flash", "glm-4-plus", "glm-4-air"],
    "Kimi(Moonshot)": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
}

# 兼容旧版：仅用于读取旧配置时的模型名升级
COMMON_MODELS = [m for lst in MODELS_BY_PLATFORM.values() for m in lst]


def _http_hint(code, base, model):
    """Map an HTTP status code to a troubleshooting suggestion."""
    if code in (401, 403):
        return ("Auth failed: 1) is the API Key complete (no leading/trailing spaces)? 2) does the Key match the selected platform"
                "(Volcano Ark Keys cannot be used on OpenAI/DeepSeek endpoints)? 3) for Volcano Ark use the console-created "
                "APIKey (UUID format), and enable the model/endpoint in the console.")
    if code == 404:
        return (f"Wrong address or model name: confirm the API path is complete and model {model!r} is available for your account."
                "Volcano Ark note: copy the lowercase model ID from console 'Manage' (e.g. doubao-seed-2-0-lite-260428)"
                "or endpoint ID (ep-...); do NOT copy the display name (e.g. Doubao-Seed-2.0-lite 260428),"
                "and do not select an unenabled model.")
    if code == 429:
        return "Too many requests or insufficient quota: retry later, or check balance/rate limits."
    if code >= 500:
        return "Platform server temporarily down: retry later."
    return ""


def normalize_model_name(model, base_url=""):
    """把用户手动输入的模型名规范化为平台能识别的格式。
    火山方舟：控制台显示名（如 Doubao-Seed-2.0-lite 260428）→ API 模型 ID
    （doubao-seed-2-0-lite-260428）：空格压缩为连字符、小写、点转连字符；推理接入点 ID（ep- 开头）只去空格。
    其他平台：只去首尾空白。"""
    m = (model or "").strip()
    if not m:
        return m
    base = (base_url or "").lower()
    if "volces.com" in base or "ark" in base:
        if m.startswith("ep-"):
            return m.replace(" ", "")
        return re.sub(r"\s+", "-", m).replace(".", "-").lower()
    return m


def _diag_network(url, connect_timeout=15):
    """Quickly diagnose DNS resolution and TCP connectivity to distinguish network vs API issues."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        t0 = time.time()
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        dns_t = time.time() - t0
        addrs = sorted({i[4][0] for i in infos[:5]})
        t0 = time.time()
        sock = socket.create_connection((host, port), timeout=connect_timeout)
        sock.close()
        tcp_t = time.time() - t0
        return (f"DNS resolve {host} → {addrs} ({dns_t:.2f}s);"
                f"TCP connect {host}:{port} OK ({tcp_t:.2f}s). Network layer reachable,"
                f"timeout happens while waiting for the LLM server to return data.")
    except Exception as e:
        return f"Network diag: {e} (an error here means DNS/TCP connectivity itself is broken)"


# 本程序所有 LLM 调用（发送+接收）统一记录，运行/调试后写入 Memory/llm_chat_log.txt
LLM_DIALOG_LOG = []


def _flush_dialog_log():
    try:
        _mem = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memory")
        os.makedirs(_mem, exist_ok=True)
        _parts = []
        for _i, _d in enumerate(LLM_DIALOG_LOG, 1):
            _parts.append("[Dialogue %d%s]\n[SEND]\n%s\n[RECV]\n%s" % (
                _i, "(mock)" if _d.get("mock") else "",
                json.dumps(_d.get("send", []), ensure_ascii=False, indent=1),
                _d.get("receive", "")))
        save_text(os.path.join(_mem, "llm_chat_log.txt"), "\n\n".join(_parts))
    except Exception:
        pass


def _open_result_file():
    """After finishing, open Memory/final_result.txt with the system default program so the user can see the result."""
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memory", "final_result.txt")
        if not os.path.exists(_p):
            return
        if sys.platform.startswith("win"):
            os.startfile(_p)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", _p])
        else:
            subprocess.Popen(["xdg-open", _p])
    except Exception:
        pass


# ---- 信息输入（单框）：用户要提交给 AI 的信息都填在一个输入框，保存 环境信息.json ----
ENV_INFO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "environment_info.json")


def read_env_info():
    try:
        with open(ENV_INFO_FILE, "r", encoding="utf-8-sig") as f:
            _d = json.load(f)
            return str(_d.get("info1", ""))
    except Exception:
        return ""


def save_env_info(text):
    try:
        save_text(ENV_INFO_FILE, json.dumps({"info1": text or ""}, ensure_ascii=False, indent=1))
    except Exception:
        pass


def env_info_text():
    """Combine the info-box content into readable text (for Prompt injection)."""
    return read_env_info() or "(empty)"


def env_need_check(cfg, emit=print, ask_user=None):
    """采集前让 LLM 判断：按当前采集方案是否需要用户补充登录/个人资料等信息。
    需要时把 AI 的填写说明显示给用户，并（GUI 模式）等待用户填好输入栏后回车继续。"""
    try:
        _prompt = (
            "Task:" + TASK + "\n\n"
            "Collection plan (generated by Program1):\n" + COLLECT_PLAN + "\n\n"
            "User-provided info in the input box (may be empty):\n"
            + env_info_text() + "\n\n"
            "Judge: to collect per this plan, do we need extra login / personal info"
            "(e.g. account, password, token credentials)?\n"
            "If not needed, reply ONLY: not_needed\n"
            "If needed, clearly state what the user should fill in the Info input box, e.g.:\n"
            "  Please fill in the info box:\n"
            "  account = your account\n"
            "  password = your password\n"
            "  (list each item as needed, one per line)\n"
            "(all info goes into the same box, one item per line)"
        )
        emit("[ENV] asking LLM whether collection info is sufficient...")
        reply = llm_ask([{"role": "user", "content": _prompt}], cfg)
        emit("[ENV] AI judge reply:")
        for _line in (reply or "").splitlines()[:30]:
            emit("  | " + _line)
        _need = bool(reply) and "not_needed" not in reply and any(
            kw in reply for kw in ("fill in", "Please enter", "Please provide", "please tell", "Please give",
                                   "needs you", "needs you", "token", "account", "password", "credentials",
                                   "server address", "username"))
        # 信息输入方式：window3=输入在程序3 窗口（需要输入时跳过窗口2 采集）；
        #              window2=输入在程序2 窗口（运行采集，采集阶段暂停等用户输入）
        if cfg.get("input_mode", "window3") != "window2" and _need:
            emit("[ENV] AI says user input needed (e.g. account/password/token).")
            emit("[ENV] input mode window3: skip Program2 collection; generate & run Program3 directly; input collected in Program3 window.")
            return True
        return False
    except Exception as e:
        emit(f"[ENV] judge failed (skip; collect per original plan): {e}")
        return False


def call_llm(messages, cfg):
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    if not base.startswith("http://") and not base.startswith("https://"):
        raise RuntimeError(f"Invalid API base: {base!r} (must start with http:// or https://)")
    if not key:
        raise RuntimeError("API Key empty: paste it in the GUI config area, or set the ARK_API_KEY / DOUBAO_API_KEY env var")
    model = normalize_model_name(cfg.get("model", ""), base)
    _web = bool(cfg.get("web_search"))
    if _web:
        # 联网模式：方舟联网内容插件 web_search 仅支持 Responses API（/responses），chat/completions 会 400
        url = base + "/responses"
        _last_user = None
        for _m in reversed(messages):
            if _m.get("role") == "user":
                _last_user = _m
                break
        if _last_user is None:
            _last_user = messages[-1] if messages else {"role": "user", "content": ""}
        payload = {
            "model": model,
            "input": [{"role": _last_user.get("role", "user"),
                        "content": [{"type": "input_text", "text": str(_last_user.get("content", ""))}]}],
            "tools": [{"type": "web_search"}],
            "stream": False,
        }
    else:
        url = base + "/chat/completions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        # 直连：绕过系统代理（系统代理会在空闲 30s 左右掐断长请求）
        _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with _opener.open(req, timeout=cfg.get("timeout", 280)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:600]
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[HTTP error {e.code}] {detail}", "ok": False})
        hint = _http_hint(e.code, base, model)
        raise RuntimeError(
            f"LLM API error {e.code}\n"
            f"Request URL: {url}\n"
            f"Platform response: {detail}\n"
            + (("Troubleshooting: " + hint) if hint else ""))
    except Exception as e:
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[ERROR] {e}", "ok": False})
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            diag = _diag_network(url)
            prox = [p for p in ("http_proxy", "https_proxy", "all_proxy")
                    if os.environ.get(p) or os.environ.get(p.upper())]
            prox_txt = ("System proxy env detected: " + ", ".join(prox) +
                        " → if curl also times out, temporarily disable the proxy/VPN and retry." if prox
                        else "No system proxy env detected.")
            curl_body = json.dumps(payload, ensure_ascii=False)[:300]
            raise RuntimeError(
                f"LLM request timeout (waited {cfg.get('timeout', 280)}s): prompt too long or network slow.\n"
                f"Request URL: {url}\nRaw error: {e}\n{diag}\n{prox_txt}\n"
                f"Self-test command (run in terminal; replace <your API Key> to check whether it is an API issue):\n"
                f"  curl -sS -m 60 {url} \\\n"
                f"    -H 'Authorization: Bearer <your API Key>' -H 'Content-Type: application/json' \\\n"
                f"    -d '{curl_body}'\n"
                f"Judge: 1) curl also times out → network/proxy issue; 2) curl quickly returns 401/404 → Key or model-name issue"
                f"(see the 'Test connection' button's suggestions); 3) curl returns fine → raise 'Call timeout' to 600s and retry.")
        raise RuntimeError(f"Cannot connect to LLM API ({base}); check network or address: {e}")
    try:
        if _web:
            _txts = []
            for _o in (body.get("output") or []):
                if _o.get("type") == "message":
                    for _c in (_o.get("content") or []):
                        if _c.get("type") == "output_text":
                            _txts.append(_c.get("text", ""))
            content = "\n".join(_txts)
            if not content:
                raise KeyError("output_message_empty")
        else:
            content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Unexpected LLM reply format: {str(body)[:300]}")
    LLM_DIALOG_LOG.append({"send": messages, "receive": content, "ok": True})
    return content


def llm_ask(messages, cfg=None):
    cfg = cfg or CONFIG
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        text = messages[-1]["content"]
        if text.startswith("Recognize per the criteria below"):
            r = MOCK_REPLY
        else:
            r = MOCK_COLLECT
        LLM_DIALOG_LOG.append({"send": messages, "receive": r, "ok": True, "mock": True})
        _flush_dialog_log()
        return r
    r = call_llm(messages, cfg)  # call_llm 内已记录
    _flush_dialog_log()
    return r


def strip_code_fence(text):
    text = (text or "").strip()
    blocks = re.findall(r"```[a-zA-Z]*\s*\n?(.*?)```", text, re.S)
    if blocks:
        return "\n\n".join(b.strip() for b in blocks if b.strip())
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    return text


# ---- AI 代码最终复查（程序3 生成前）：语法 + import 白名单 ----
_P3_ALLOWED_IMPORTS = {
    "json", "os", "sys", "re", "time", "threading", "subprocess", "csv", "io", "collections",
    "math", "random", "datetime", "urllib", "socket", "base64", "hashlib", "pathlib", "glob",
    "functools", "itertools", "traceback", "contextlib", "typing", "string", "textwrap",
    "sqlite3", "shutil", "tempfile", "platform", "statistics", "decimal", "fractions",
    "dataclasses", "enum", "abc", "copy", "pprint", "warnings", "unicodedata", "codecs",
    "locale", "signal", "queue", "logging", "batch_util", "__future__", "ast",
    "imaplib", "poplib", "smtplib", "email", "mailbox",
}

def _check_ai_code(code, label="AI code"):
    """程序3 生成前对 AI 代码做最终复查：语法 + import 白名单。
    返回 (ok, problems)。失败则不生成程序3（避免生成必败程序）。"""
    problems = []
    for _m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M):
        _top = _m.group(1).split(".")[0]
        if _top not in _P3_ALLOWED_IMPORTS:
            problems.append(
                f"{label}: used a library outside the tool list: {_m.group(1)}"
                f"(only built-in batch_util and stdlib allowed; third-party libs forbidden)")
    try:
        compile(code, "<p3_check>", "exec")
    except SyntaxError as _e:
        problems.append(f"{label}: syntax error {_e}")
    return (not problems), problems


def save_text(path, text):
    # utf-8-sig 带 BOM，Windows 记事本直接打开不乱码
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text or "")


def decode_bytes(b):
    """Decode subprocess output via utf-8 → gbk → latin-1 in turn to avoid garbled text on Windows."""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    return b.decode("utf-8", "replace")


def summary(text, n=200):
    t = (text or "").replace("\n", " ").strip()
    return t[:n] + ("..." if len(t) > n else "")


def _key_store_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memory", "api_key.txt")


def _load_persisted_key():
    p = _key_store_path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def persist_key(cfg):
    """把用户新填的 API Key 持久化到本地密钥文件（界面仍不显示、不回填）。
    这样测试成功后重开程序 / 自动流程都能用上新 Key，不会退回训练时的旧 Key。"""
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return
    try:
        os.makedirs(os.path.dirname(_key_store_path()), exist_ok=True)
        with open(_key_store_path(), "w", encoding="utf-8") as f:
            f.write(key)
    except Exception:
        pass


def load_local_config():
    """若存在 主程序配置.json（GUI 保存过），用它覆盖内嵌默认配置。
    API Key 优先级：本地密钥文件（用户测试后保存的新 Key）> 训练时内嵌 CONFIG。
    密钥只用于调用，绝不回填界面输入框（界面打开始终为空）。"""
    folder = os.path.dirname(os.path.abspath(__file__))
    cfg = dict(CONFIG)
    path = os.path.join(folder, "main_config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    # 安全策略：API Key 永不从 主程序配置.json 回填；优先用用户测试后保存的密钥文件
    key = _load_persisted_key()
    if key:
        cfg["api_key"] = key
    elif not cfg.get("api_key"):
        cfg["api_key"] = CONFIG.get("api_key", "")
    # 兼容旧配置：旧/已下线模型名自动回退到默认，避免实际调用 404
    if cfg.get("model") in ("doubao-seed-1-6-250615", "doubao-1-5-pro-32k-250115",
                            "doubao-seed-2-1-pro-260628", "", None):
        cfg["model"] = CONFIG.get("model") or "doubao-seed-2-0-lite-260428"
    cfg["model"] = normalize_model_name(cfg.get("model", ""), cfg.get("base_url", ""))
    return cfg


def save_local_config(cfg):
    folder = os.path.dirname(os.path.abspath(__file__))
    data = {k: v for k, v in cfg.items() if k != "api_key"}
    with open(os.path.join(folder, "main_config.json"), "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def collect_info(cfg, emit=print, ask_user=None, mode="auto"):
    """【信息收集】两步：
    第一步 收集：按训练生成的收集方案执行——有脚本则运行脚本拿真实原始信息，否则发 Prompt 给 LLM 收集；
    第二步 转换：把原始信息交给 LLM，转成严格 JSON，只标每个 state 的当前取值（不含 choice）。
    如果收集到的信息表明需要用户补充输入（如"Please enter..."），会暂停并调用 ask_user(问题) 等待用户回答。
    mode: "auto"= script if available, otherwise LLM;"local"=强制本地脚本采集（无脚本则报错）；
          "llm"=强制 LLM 收集（跳过脚本分支）。
    返回 state 取值 JSON 字符串；失败返回 None。"""
    folder = os.path.dirname(os.path.abspath(__file__))
    sub_dir = os.path.join(folder, "subfolder")
    mem_dir = os.path.join(folder, "Memory")
    os.makedirs(sub_dir, exist_ok=True)
    os.makedirs(mem_dir, exist_ok=True)

    # ---- 检测是否需要用户补充信息 ----
    def needs_user_input(text):
        """检测是否需要用户补充输入。
        只检测 LLM 采集回复里明确的"ask user input"指令；
        程序抓取的网页内容属于数据，不参与判定（网页 HTML 里 ?/请输入 到处都是）。"""
        if not text:
            return False
        # 切掉【程序抓取的网页内容】及之后的部分：网页 HTML 里的 ?/请输入 到处都是，不能参与判定
        idx = text.find("[webpage content captured by the program")
        if idx >= 0:
            text = text[:idx]
        # 再切掉网页正文段（LLM 回复常以『网页内容：…』之类结尾）
        idx2 = text.find("webpage content")
        if 0 <= idx2 < len(text) and idx2 > 50:
            text = text[:idx2]
        patterns = ["Please enter", "Please provide", "tell me", "Please give", "please fill in", "please choose", "please inform", "please supplement"]
        return any(p in text for p in patterns)

    # 从 RESULT1 提取所有 state 名（脚本模式校验完整性用）
    expected_states = set()
    try:
        _r1 = extract_json(RESULT1) or {}
        for _s in (_r1.get("states") or []):
            if isinstance(_s, dict) and _s.get("name"):
                expected_states.add(_s["name"])
    except Exception:
        pass

    # ---- 第一步：信息收集（完全靠 LLM 采集，不运行任何本地脚本）----
    emit("[COLLECT] Commanding LLM to collect info per the plan...")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # 直接粘贴完整的收集方案原文（不用 JSON 提取，兼容文字描述格式）
    plan_note = COLLECT_PLAN if COLLECT_PLAN else "(no collect plan; collecting per RESULT1 state definitions)"
    collect_prompt_text = (
        f"Collect the info needed to complete task \"{TASK}\".\n"
        f"Current system time: {now}\n\n"
        "Below are the state definitions and criteria (RESULT1):\n" + RESULT1 + "\n\n"
        "Information collection plan (RESULT2):\n" + plan_note + "\n\n"
        "Collect, per the plan, the info needed for each state's current real value (real-time data, user input,"
        "environment/business data...), state the source & method, and give the most likely/latest raw info.\n"
        "Do NOT output JSON; output the raw collected info (text or key-value list).\n\n"
        "Environment info (user-provided, may be empty; use for login/credentials):\n" + env_info_text() + "\n\n"
        "[IMPORTANT] You have web search (web_search); for states needing real-time/external data (weather, quotes, webpages),\n"
        "get real latest info directly via web search and state the source;\n"
        "do NOT declare URLs or ask local programs to fetch webpages.\n"
        + BUILTIN_DATA_URLS +
        "(you may also declare other public JSON APIs.)\n"
    )
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        emit("[COLLECT] (mock) using mock collection result")
    raw = llm_ask([{"role": "user", "content": collect_prompt_text}], cfg)
    emit("[COLLECT] Raw collected info: " + summary(raw, 400))

    # ---- 联网搜索模式：不本地抓取网页；LLM 通过 web_search 直接获取实时信息 ----
    if re.search(r"FETCH_URLS\s*starts", raw or ""):
        emit("[COLLECT-LLM] NOTE: online search mode; no local scraping; LLM-declared URLs ignored; the LLM searches online itself.")

    # ---- 检测是否需要用户补充信息，暂停等待 ----
    # ---- 需要用户补充输入：按「信息输入方式」分流 ----
    # window2：在程序2 窗口暂停等待用户输入后继续转换；window3：跳过窗口2 暂停，占位继续，输入在程序3 窗口
    if needs_user_input(raw):
        if cfg.get("input_mode", "window3") == "window2" and ask_user is not None:
            emit("[COLLECT] user supplement needed (window2 mode); pausing...")
            question = raw.strip()[:500] if raw.strip() else "Please provide the supplementary info needed to complete the task"
            emit(f"[COLLECT] Question: {question}")
            user_answer = ask_user(question)
            if user_answer and user_answer.strip():
                emit(f"[COLLECT] User added: {summary(user_answer, 200)}")
                raw = raw.strip() + "\n\n[User supplement]" + user_answer.strip()
            else:
                emit("[COLLECT] user added nothing; continuing with original info...")
        else:
            emit("[COLLECT] user supplementary input needed (login/params); no pause-wait condition here"
                  "(window3 mode, or no input UI in this environment); not waiting in the Program2 window.")
            emit("[COLLECT] Program3 will be generated and run; its window will prompt you to fill in any needed input (e.g. account/password/token).")
            emit("[COLLECT] AI asks for supplement: " + summary(raw, 300))
            placeholder = {s: "to be entered by the user at Program3 runtime" for s in expected_states}
            state_json = json.dumps(placeholder, ensure_ascii=False)
            save_text(os.path.join(mem_dir, "raw_collected.txt"), raw)
            # 占位结果不写入 识别结果.txt 缓存：避免下次运行误复用占位而跳过采集
            emit("[COLLECT] continuing with placeholder state values (real input at Program3 runtime): " + summary(state_json, 200))
            return state_json

    save_text(os.path.join(mem_dir, "raw_collected.txt"), raw)

    # ---- 第二步：转换（LLM 把原始信息转成每个 state 取值 JSON，只含 state，不含 choice）----
    emit("[CONVERT] Sending raw info to LLM to convert into state-value JSON...")
    convert_prompt = (
        "Recognize the current actual states of the task by the criteria below, and strictly output in JSON format " + RESULT1 + "：\n"
        "Below is the raw collected info and web-search content (real data for recognizing state values):\n"
        + (raw or "(no raw info)") + "\n\n"
        "If web search (web_search) is enabled and info is insufficient, search the web to supplement before judging.\n"
        "Judge each state's current value from the info (fill gaps reasonably by common sense).\n"
        "Mark the current value of EVERY state. "
        "[IMPORTANT] Every state MUST have a value; do not miss any. "
        "Output state values ONLY, not choice (choice is the later executed action). "
        "[Output MUST be flat JSON] directly state_name → value; no need_loop/states wrapper, no nesting. "
        'Example format: {"Gold_Price": "mid-range sideways zone", "24h_Change": "sharp decline", ...}. '
        "No explanation text, markdown markers or code fences."
    )
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        emit("[CONVERT] (mock) using mock state-value JSON")
    # 独立发送：只发当前 Prompt + 粘贴原始信息，不用对话历史
    state_json = llm_ask([{"role": "user", "content": convert_prompt}], cfg)

    # ---- 第三步：JSON 格式校验 + 完整性校验（不符合自动重发，最多 3 次）----
    emit("[CHECK] Verifying LLM reply is valid JSON and every state has a value...")

    def flatten_state_json(parsed):
        """Compatible nested wrapper: {"need_loop":..., "states": {"name": "value"} → flat {"name": "value"}。"""
        if not isinstance(parsed, dict):
            return parsed
        _st = parsed.get("states")
        if isinstance(_st, dict):
            parsed = dict(_st)
        elif isinstance(_st, list):
            _flat = {}
            for _item in _st:
                if isinstance(_item, dict) and _item.get("name"):
                    _flat[_item["name"]] = (_item.get("value") or _item.get("value")
                                            or (_item.get("values") or [""])[0] if isinstance(_item.get("values"), list) else _item.get("values"))
            if _flat:
                parsed = _flat
        parsed.pop("need_loop", None)
        return parsed

    def validate_state_json(text):
        """Validate JSON format + every state has a value. Returns (ok, missing_states, parsed)."""
        parsed = extract_json(text)
        if parsed is None:
            return False, expected_states, None
        parsed = flatten_state_json(parsed)
        missing = []
        for s in expected_states:
            if s not in parsed:
                missing.append(s)
        if missing:
            return False, set(missing), parsed
        return True, set(), parsed

    retry_count = 0
    # 独立发送：每次重试都把 raw、上次回复、校验原因都粘贴进去，不用对话历史
    while True:
        ok, missing, parsed = validate_state_json(state_json)
        if ok:
            break
        retry_count += 1
        if retry_count > 3:
            break
        emit(f"[CHECK {retry_count}/3] validation failed; asking LLM to re-answer...")
        if missing == expected_states and parsed is None:
            # JSON 格式不对
            emit(f"[CHECK {retry_count}/3] reason: not valid JSON")
            retry_prompt = (
                "Strictly output in JSON format.\n\n"
                "State definitions (RESULT1):\n" + RESULT1 + "\n\n"
                "Your last reply is not strict JSON. Re-output it:\n"
                "1. Output ONLY one JSON object; no explanation, markdown fences or code markers\n"
                '2. Format MUST be: {"state1": "<value>", "state2": "<value>", ...}\n'
                "3. Values are strings; no nested objects or arrays\n"
                "4. States only, no choice\n"
                "5. Include ALL states, none missing\n"
                "6. For real-time data (weather etc.), use your web search for real latest info; no URLs.\n\n"
                "Your last reply:\n" + state_json
            )
        else:
            # JSON 格式对，但缺了某些 state
            emit(f"[CHECK {retry_count}/3] reason: missing state values: {', '.join(missing)}")
            retry_prompt = (
                "State definitions (RESULT1):\n" + RESULT1 + "\n\n"
                "Your JSON is valid but missing values for these states:"
                + ", ".join(missing) + "\n"
                "Re-output complete JSON with EVERY state value, none missing.\n"
                "Missing values may mean real-time info was not fetched: use web search (web_search) for real latest info; no URLs.\n"
                'Format: {"state1": "<value>", "state2": "<value>", ...}\n\n'
                "Your last reply:\n" + state_json
            )
        # 独立发送：只发当前重试 Prompt
        state_json = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
        emit(f"[CHECK {retry_count}/3] LLM re-answer: " + summary(state_json, 300))

    ok, missing, parsed = validate_state_json(state_json)
    if ok:
        state_json = json.dumps(parsed, ensure_ascii=False)   # 拍平后重写，保证下游格式一致
        emit("[CHECK] JSON valid and all states have values! Moving on...")
    else:
        reason = "not valid JSON" if not parsed else f"missing state values: {', '.join(missing)}"
        emit(f"[CHECK] failed 3 times ({reason}); user input needed...")

    # ---- 3 次仍失败：暂停让用户选择处理方式 ----
    if not ok and ask_user is not None:
        emit("[CHECK] failed 3 times; pausing for user...")
        emit(f"[CHECK] LLM actual output: {summary(state_json, 500)}")
        emit(f"[CHECK] Problem: {reason}")
        question = (
            "LLM output:\n" + summary(state_json, 300) + "\n\n"
            f"Problem: {reason}\n\n"
            "Choose your collection plan:\n"
            "1. AI Random: let the LLM generate random state values\n"
            "2. AI Improve: let the LLM improve the collection plan, then recollect\n"
            "3. Manual: enter correct JSON directly (e.g. {\"state1\": \"sunny\"}) or supplementary info\n"
            "(or go back to Program1 for feedback fixing)"
        )
        user_input = ask_user(question)
        if user_input and user_input.strip():
            user_input = user_input.strip()
            if user_input == "random" or user_input == "random":
                emit("[CONVERT] user chose random; commanding LLM to generate random state values...")
                rand_prompt = (
                    "Generate a reasonable random value for each state from the definitions below,"
                    "and strictly output JSON (states ONLY, no choice):\n\n" + RESULT1 +
                    "\n\nOutput ONLY a JSON object; no explanation or markdown."
                )
                state_json = llm_ask([{"role": "user", "content": rand_prompt}], cfg)
                emit("[CONVERT] AI random: " + summary(state_json, 300))
            elif user_input == "improve" or user_input == "improve":
                emit("[CONVERT] user chose improve; commanding LLM to improve the collect plan...")
                improve_prompt = (
                    "The previous collection plan was insufficient; valid state values could not be obtained.\n"
                    "Based on the task \"" + TASK + "\" and the state definitions, redesign a more complete collect plan,"
                    "Ensure every state value can be accurately collected.\n\n"
                    "State definitions:\n" + RESULT1 + "\n\n"
                    "Previous raw info:\n" + raw + "\n\n"
                    "Output the new collection plan in JSON, format:\n"
                    '{"states_collect": [{"state": "state1", "method": "prompt", "source": "clearer info sources"}, ...]}\n'
                    "Output JSON only, no explanation."
                )
                new_plan = llm_ask([{"role": "user", "content": improve_prompt}], cfg)
                emit("[CONVERT] LLM improved plan: " + summary(new_plan, 300))
                # 重新采集（用新方案）
                plan = extract_json(new_plan) or {}
                states_collect = plan.get("states_collect") or []
                desc = []
                for item in states_collect:
                    if isinstance(item, dict):
                        desc.append(f"- state \"{item.get('state')}\" source: {item.get('source') or 'unknown'}")
                plan_note = "\n".join(desc) if desc else ""
                recollect_prompt = (
                    f"Using the improved plan below, collect the needed info for EVERY state's current value of task [{TASK}].\n\n"
                    "Collection plan:\n" + plan_note + "\n\n"
                    "State definitions:\n" + RESULT1 + "\n\n"
                    "Output raw info directly, no JSON."
                )
                raw = llm_ask([{"role": "user", "content": recollect_prompt}], cfg)
                emit("[CONVERT] Recollected: " + summary(raw, 300))
                # 再转换一次
                convert_prompt2 = (
                    IDENTIFY_PREFIX + RESULT1 + "\n\nCurrently collected raw info:\n" + raw +
                    "\n\nConvert the raw info into strict JSON per the criteria, stating every state's current value."
                    "If web search is enabled and info is insufficient, search the web to supplement."
                    "Output ONLY the JSON object; no explanation or markdown."
                )
                state_json = llm_ask([{"role": "user", "content": convert_prompt2}], cfg)
                emit("[CONVERT] Improved conversion: " + summary(state_json, 300))
            elif user_input.startswith("{") and extract_json(user_input):
                state_json = user_input
                emit("[CONVERT] user provided valid JSON directly")
            else:
                emit(f"[CONVERT] user added info: {summary(user_input, 200)}; asking LLM to convert...")
                raw_with_user = raw.strip() + "\n\n[User supplement]" + user_input
                retry_prompt = (
                    "Convert the info to strict JSON per the criteria and user-added info, marking every state's current value."
                    "Output ONLY a JSON object; no explanation or markdown.\n\n" +
                    "Recognition criteria:\n" + RESULT1 + "\n\n" +
                    "Info:\n" + raw_with_user
                )
                state_json = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
                emit("[CONVERT] LLM conversion: " + summary(state_json, 300))
        else:
            emit("[CONVERT] user gave nothing; continuing with original result...")

    save_text(os.path.join(mem_dir, "state_values.txt"), state_json)
    emit("[CONVERT] state-value JSON saved to Memory/state_values.txt: " + summary(state_json, 300))
    sj = extract_json(state_json)
    if sj is None:
        emit("[CONVERT] WARN: still not valid JSON; raw text saved; fix via Program1 feedback.")
    return state_json


def run_auto(cfg, emit=print, popup=False, ask_user=None, force_mode=None):
    """force_mode: None=自动（LLM 采集/复用）；
    "llm"= force LLM collection and compute;"local"= force local-script collection and compute."""
    STOP_REQUESTED[0] = False  # 每次运行前重置停止标志（「停止」按钮置 True 后各阶段提前退出）
    folder = os.path.dirname(os.path.abspath(__file__))
    sub_dir = os.path.join(folder, "subfolder")
    mem_dir = os.path.join(folder, "Memory")
    os.makedirs(sub_dir, exist_ok=True)
    os.makedirs(mem_dir, exist_ok=True)

    # 第〇步：让 LLM 判断采集信息是否足够；需要用户输入时跳过程序2 采集，交给程序3 窗口收集
    need_user_input = env_need_check(cfg, emit=emit, ask_user=ask_user)

    # 第一步：信息收集（完全靠 LLM 采集；需要用户输入 → 占位继续，输入在程序3 窗口收集）

    if STOP_REQUESTED[0]:
        emit("[STOP] Stop requested; collection not started")
        return -1

    if need_user_input:
        emit("[COLLECT] user input needed (credentials/params); skipping Program2 collection; generating and running Program3 directly.")
        _expected = set()
        try:
            _r1j = extract_json(RESULT1) or {}
            for _s in (_r1j.get("states") or []):
                if isinstance(_s, dict) and _s.get("name"):
                    _expected.add(_s["name"])
        except Exception:
            pass
        r4 = json.dumps({_s: "to be entered by the user at Program3 runtime" for _s in _expected}, ensure_ascii=False)
        # 占位结果不写入 识别结果.txt 缓存：避免下次运行误复用占位而跳过采集
        emit("[COLLECT] continuing with placeholder state values (real input at Program3 runtime): " + summary(r4, 200))
    elif force_mode == "llm":
        emit("[COLLECT] forcing LLM collection (no local scripts)...")
        r4 = collect_info(cfg, emit, ask_user=ask_user, mode="llm")
        if r4 is None:
            emit("[ERROR] LLM collection failed; Program3 not started.")
            return -1
    elif cfg.get("input_mode", "window3") == "window2":
        emit("[COLLECT] input mode window2: forcing Program2 collection (no reuse of old results)...")
        r4 = collect_info(cfg, emit, ask_user=ask_user, mode="auto")
        if r4 is None:
            emit("[ERROR] info collection failed; Program3 not started.")
            return -1
    else:
        rec_path = os.path.join(mem_dir, "state_values.txt")
        if os.path.exists(rec_path):
            with open(rec_path, "r", encoding="utf-8-sig") as f:
                r4 = f.read()
            emit("[COLLECT] reusing converted state-value JSON: " + summary(r4, 300))
        else:
            r4 = collect_info(cfg, emit, ask_user=ask_user, mode="auto")
            if r4 is None:
                emit("[ERROR] info collection failed; Program3 not started.")
                return -1

    # ---- 严格校验：JSON 格式 + 所有 state 都有取值 ----
    # 校验不通过时自动给 LLM 发 prompt 重试（最多 3 次），3 次不行才报错
    expected_states = set()
    try:
        r1_json = extract_json(RESULT1) or {}
        for s in (r1_json.get("states") or []):
            if isinstance(s, dict) and s.get("name"):
                expected_states.add(s["name"])
    except Exception:
        pass

    def validate_r4(text):
        parsed = extract_json(text)
        if parsed is None:
            return False, "not valid JSON", parsed
        parsed = flatten_state_json(parsed)
        missing = [s for s in expected_states if s not in parsed]
        if missing:
            return False, f"missing state values: {', '.join(missing)}", parsed
        return True, "", parsed

    r4_valid, r4_reason, r4_parsed = validate_r4(r4)
    retry = 0
    while not r4_valid and retry < 3:
        retry += 1
        emit(f"[CHECK {retry}/3] invalid ({r4_reason}); sending prompt to LLM for strict format...")
        retry_prompt = (
            "Strictly provide every state and its value in JSON. Your previous output had problems:" + r4_reason + "\n\n"
            "Requirements:\n"
            "1. Output ONLY one JSON object; no explanation or markdown\n"
            "2. Must include ALL states, each with a value\n"
            "3. Format: {\"state1\": \"<value>\", \"state2\": \"<value>\", ...}\n"
            "4. For real-time data (weather etc.), use your online search ability (web_search) to fetch real latest info; do NOT declare URLs.\n\n"
            "Previous output:\n" + summary(r4, 500) + "\n\n"
            "State definitions:\n" + RESULT1 + "\n\n"
            "Output strict JSON again."
        )
        r4 = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
        emit(f"[CHECK {retry}/3] LLM re-answer: " + summary(r4, 300))
        r4_valid, r4_reason, r4_parsed = validate_r4(r4)

    if not r4_valid:
        emit(f"[ERROR] still invalid after 3 retries ({r4_reason}); Program3 not started.")
        emit(f"[ERROR] LLM actual output: {summary(r4, 800)}")
        return -1

    emit("[CHECK] state-value JSON valid (format OK + all states have values); starting Program3...")

    # 第三步：组装 → 生成 run_program.py（程序3）= 结果2(运算程序) + 结果3(执行程序+窗口代码)
    emit("[MAIN] assembling RESULT2 (compute) + RESULT3 (executor+window code)...")
    blocks = [strip_code_fence(x) for x in (RESULT2, RESULT3)]
    blocks = [b for b in blocks if b]
    # AI 代码最终复查：语法 + import 白名单（通过才生成程序3，避免生成必败程序）
    for _b in blocks:
        _ok, _errs = _check_ai_code(_b)
        if not _ok:
            emit("[ERROR] AI code final check failed; Program3 not started: " + "; ".join(_errs))
            return -1
    emit("[CHECK] AI code final check passed (syntax + import whitelist)")
    context = (
        "# -*- coding: utf-8 -*-\n"
        "# auto-generated program (Program3, generated by main_program.py)\n"
        "# Task: " + TASK + "\n"
        "\n"
        + P3_CONTRACT_TEXT + "\n"
        "import threading\n"
        "import json\n"
        "import os\n"
        "\n"
        "# ---- built-in batch_util (auto-injected; no pip install) ----\n"
        + BATCH_UTIL_SOURCE + "\n"
        "# ---- Program1 info (fully kept; Program3 standalone; nothing lost) ----\n"
        "TASK = " + repr(TASK) + "\n"
        "CONFIG = " + repr(cfg) + "\n"
        "RESULT1 = " + repr(RESULT1) + "\n"
        "RESULT2 = " + repr(RESULT2) + "\n"
        "RESULT3 = " + repr(RESULT3) + "\n"
        "RESULT4 = " + repr(r4) + "\n"
        "RESULT6 = " + repr(RESULT6) + "\n"
        "WINDOW_CODE = " + repr(WINDOW_CODE) + "\n"
        "COLLECT = " + repr(r4) + "\n"
        "ENV_INFO = " + json.dumps(read_env_info(), ensure_ascii=False) + "\n"
        "\n"
        "# ---- output helper (full auto-run: no popups, no click waits; all via print) ----\n"
        "_win_ns = {\"RESULT4\": RESULT4}\n"
        "_win_code = " + repr(strip_code_fence(WINDOW_CODE)) + "\n"
        "try:\n"
        "    exec(compile(_win_code, \"<window_code>\", \"exec\"), _win_ns)\n"
        "except Exception:\n"
        "    _win_ns = {\"RESULT4\": RESULT4}\n"
        "if \"show_text_result\" in _win_ns:\n"
        "    show_text_result = _win_ns[\"show_text_result\"]\n"
        "else:\n"
        "    def show_text_result(msg):\n"
        "        print('[Decision] ', str(msg))\n"
        "if \"run_script_with_window\" in _win_ns:\n"
        "    run_script_with_window = _win_ns[\"run_script_with_window\"]\n"
        "else:\n"
        "    def run_script_with_window(code, title=\"executing script\"):\n"
        "        try:\n"
        "            exec(compile(code, \"<script>\", \"exec\"), {\"__name__\": \"__main__\", \"RESULT4\": RESULT4})\n"
        "            print('[Decision] script executed')\n"
        "        except Exception as e:\n"
        "            print('[Decision] script failed:', e)\n"
        "\n"
        "# ---- generated via LLM-collection path: RESULT4 is the LLM-collected & converted state-value JSON; Program3 collects nothing, only outputs JSON decision and calls scripts / writes result files ----\n"
        "\n"
        "# ---- AI-generated core code (RESULT2 compute flow + RESULT3 executor) ----\n"
        "_BLOCKS = " + repr("\n\n# ============ module separator ============\n\n".join(blocks)) + "\n"
        "\n"
        + WINDOW_SHELL_SOURCE + "\n"
    )
    program = context
    py3 = os.path.join(sub_dir, "run_program.py")
    save_text(py3, program)
    emit("[MAIN] Program3 generated: " + py3)

    # 第四步：运行 程序3，把识别结果 JSON 作为输入传入，解析并处理其 JSON 决策
    # 说明：不再自动核验反馈（两轮自动反馈已移除）。如需修正，在程序1 输入问题并点击「发送反馈并修正」。
    # 是否循环由训练时 LLM 判断（need_loop 字段）；输出为空则报错停止
    need_loop = False
    try:
        r1_json = extract_json(RESULT1) or {}
        need_loop = bool(r1_json.get("need_loop", False))
    except Exception:
        pass
    if need_loop:
        emit("[MAIN] this task needs loops (continuous monitoring / real-time updates)")
    else:
        emit("[MAIN] this is a one-shot task; runs once")

    loop_count = 0
    max_loops = 3  # 最多循环 3 次，避免无限循环
    while True:
        if STOP_REQUESTED[0]:
            emit("[STOP] Stop requested; flow aborted")
            break
        rc = run_program3(cfg, emit=emit, popup=popup,
                          extra_env={"MAIN_RESULT4": r4})
        loop_count += 1
        emit(f"[DONE] Program3 round {loop_count} finished")

        # 检查程序3 输出是否为空
        output_path = os.path.join(mem_dir, "run_output.txt")
        if os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8-sig") as f:
                output_text = f.read().strip()
            if not output_text:
                emit("[ERROR] Program3 output empty! Go back to Program1, enter the question and click 'Send feedback & fix'")
                break

        # 不需要循环，或已到最大次数，就结束
        if not need_loop:
            emit("[DONE] one-shot task; execution finished")
            break
        if loop_count >= max_loops:
            emit(f"[DONE] {max_loops} rounds done; loop ended (click 'Run' again for more)")
            break

        emit(f"[LOOP] round {loop_count + 1}: recollecting real-time info...")
        # 删除旧识别结果，强制重新采集
        if os.path.exists(rec_path):
            os.remove(rec_path)
        # 重新采集 + 转换
        r4 = collect_info(cfg, emit, ask_user=ask_user)
        # 严格校验 + 自动重试 3 次
        r4_valid, r4_reason, r4_parsed = validate_r4(r4)
        retry = 0
        while not r4_valid and retry < 3:
            retry += 1
            emit(f"[LOOP-CHECK {retry}/3] {r4_reason}; resending prompt to LLM...")
            retry_prompt = (
                "Strictly provide every state and its value in JSON. Your previous output had problems:" + r4_reason + "\n\n"
                "Requirements:\n1. Output ONLY a JSON object; no explanation or markdown\n"
                "2. Must include ALL states, each with a value\n"
                "3. Format: {\"state1\": \"<value>\", ...}\n\n"
                "Previous output:\n" + summary(r4, 500) + "\n\n"
                "State definitions:\n" + RESULT1 + "\n\nOutput strict JSON again."
            )
            r4 = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
            r4_valid, r4_reason, r4_parsed = validate_r4(r4)
        if not r4_valid:
            emit(f"[LOOP] round {loop_count + 1}: still invalid after 3 retries ({r4_reason}); stopping loop")
            emit(f"[LOOP] LLM actual output: {summary(r4, 500)}")
            break
        emit(f"[LOOP] round {loop_count + 1}: valid; running Program3...")
    _flush_dialog_log()  # 运行结束后把全部 LLM 对话（发送+接收）写入 Memory/llm_chat_log.txt
    if popup:
        _open_result_file()  # GUI 运行：任务做完后用系统默认程序打开 Memory/final_result.txt
    return rc


def test_connection(cfg, emit=print):
    """'Test LLM connection' in GUI: really send a marked message; success only when the reply contains the mark."""
    def _mask_key(k):
        k = (k or "").strip()
        return (k[:4] + "****" + k[-4:]) if len(k) > 8 else ("*" * len(k))
    # 测试连接用短超时（最多60秒）：快速失败，避免服务器hang时用户干等180秒"No response"
    test_cfg = dict(cfg)
    test_cfg["timeout"] = 60
    emit(f"[CONN TEST] Sending real test message → {cfg.get('base_url')}/chat/completions | "
         f"Model: {normalize_model_name(cfg.get('model'), cfg.get('base_url'))} | Key: {_mask_key(cfg.get('api_key'))} ...")
    emit("[CONN TEST] Waiting for LLM response (up to 60s)...")
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        reply = MOCK_CONNECTION_REPLY
        emit("[CONN TEST] (mock) returning mock reply")
    else:
        try:
            reply = call_llm([{"role": "user",
                               "content": "1"}], test_cfg)
        except Exception as e:
            emit(f"[CONN TEST] Failed: {e}")
            emit("[CONN TEST] Troubleshooting:")
            emit("  1. If timeout: pro models are reasoning models and respond slowly; raise 'Call timeout' to 300s")
            emit("  2. If 401/auth failed: check the API Key is complete with no leading/trailing spaces")
            emit("  3. If 404/model missing: confirm the model is enabled in the Volcano Ark console, or copy an endpoint ID (ep-...)")
            raise
    emit("[CONN TEST] LLM raw reply:")
    for line in (reply or "").splitlines()[:20]:
        emit("  | " + line)
    if reply and reply.strip():
        emit("[CONN TEST] Success: LLM returned content; connection OK.")
    else:
        emit("[CONN TEST] WARN: LLM returned empty.")
    folder = os.path.dirname(os.path.abspath(__file__))
    rec = os.path.join(folder, "Memory", "connection_test.txt")
    os.makedirs(os.path.dirname(rec), exist_ok=True)
    save_text(rec, reply)
    emit("[CONN TEST] Reply saved: " + rec)
    return reply


def flatten_state_json(parsed):
    """Compatible nested wrapper: {"need_loop":..., "states": {"name": "value"} → flat {"name": "value"}。"""
    if not isinstance(parsed, dict):
        return parsed
    _st = parsed.get("states")
    if isinstance(_st, dict):
        parsed = dict(_st)
    elif isinstance(_st, list):
        _flat = {}
        for _item in _st:
            if isinstance(_item, dict) and _item.get("name"):
                _flat[_item["name"]] = (_item.get("value") or _item.get("value")
                                        or (_item.get("values") or [""])[0] if isinstance(_item.get("values"), list) else _item.get("values"))
        if _flat:
            parsed = _flat
    parsed.pop("need_loop", None)
    return parsed


def extract_json(text):
    """从 LLM 返回文本中提取严格 JSON（容忍 markdown 围栏与前后缀文字）。"""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start, end, depth = -1, -1, 0
    for i, ch in enumerate(t):
        if ch == "{":
            if start < 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                end = i
                break
    if start >= 0 and end >= start:
        t = t[start:end + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


def _xinit_threads():
    try:
        import ctypes
        ctypes.CDLL("libX11.so.6").XInitThreads()
    except Exception:
        pass


def show_result_window(msg):
    """Text feedback (auto mode: no popup, just print, avoid blocking waits)."""
    print("[Decision] ", str(msg))


def run_script_window(code, title="Running script"):
    """Script feedback (auto mode: no window, execute and print)."""
    try:
        exec(compile(code, "<feedback_script>", "exec"), {"__name__": "__main__"})
        print("[Decision] script executed")
    except Exception as e:
        print("[Decision] script failed:", e)


def _save_final_result(txt):
    """把最终结果写入 Memory/final_result.txt，方便用户直接查看。"""
    try:
        folder = os.path.dirname(os.path.abspath(__file__))
        mem_dir = os.path.join(folder, "Memory")
        os.makedirs(mem_dir, exist_ok=True)
        with open(os.path.join(mem_dir, "final_result.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
    except Exception:
        pass


def extract_decision_json(text):
    """extract the part containing "decision" 字段的 JSON 决策对象。
    程序3 输出可能混有【采集】等日志 JSON，取第一个会误判；
    因此遍历所有可解析的 JSON 对象，优先返回带 decision 键的那个，
    找不到时回退到原 extract_json 行为（第一个 JSON）。"""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    candidates = []
    i = 0
    while i < len(t):
        if t[i] != "{":
            i += 1
            continue
        depth, j = 0, i
        while j < len(t):
            if t[j] == "{":
                depth += 1
            elif t[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[i:j + 1])
                        if isinstance(obj, dict):
                            candidates.append(obj)
                    except Exception:
                        pass
                    break
            j += 1
        i = j + 1
    # 取最后一个含 decision 的 JSON：程序3 可能循环多轮输出多个决策，最终决策是最后那个
    _with_d = [c for c in candidates if "decision" in c]
    if _with_d:
        return _with_d[-1]
    return candidates[0] if candidates else None


def handle_decision(out, cfg, emit=print, popup=False):
    """Handle Program3 output JSON decision: text → inform user; script → auto execute."""
    text = (out or "").strip()
    if not text:
        emit("[DECISION] Program3 has no output")
        emit("[FINAL RESULT] Program3 ran with no output (fix via Program2 'Send info to LLM')")
        _save_final_result("Program3 ran with no output (fix via Program2 'Send info to LLM').")
        return
    decision = extract_decision_json(text)
    if decision is None:
        emit("[DECISION] Program3 output below (not parsed as JSON):")
        for line in text.splitlines()[-30:]:
            emit("  | " + line)
        emit("[Final Result] " + summary(text, 300))
        _save_final_result("Program3 output (not in JSON decision format; raw output below):\n" + text)
        return
    emit("[DECISION] Program3 decision: " + summary(json.dumps(decision, ensure_ascii=False), 400))
    d = decision.get("decision", decision)
    if isinstance(d, str):
        emit("[Decision] " + d)
        emit("[Final Result] " + d)
        _save_final_result("Text result:" + d)
        if popup:
            emit(("_feedback", "text", d))
        return
    if isinstance(d, dict):
        for key in ("message", "text", "content", "msg"):
            if d.get(key):
                msg = str(d[key])
                emit("[Decision] " + msg)
                emit("[Final Result] " + msg)
                _save_final_result("Text result:" + msg)
                if popup:
                    emit(("_feedback", "text", msg))
                return
        script = d.get("script") or d.get("command") or d.get("action")
        if script:
            emit("[Decision] executing: " + str(script)[:200])
            if popup:
                # 全自动：由主程序后台线程自动执行脚本，不弹窗等待点击
                emit(("_feedback", "script", str(script)))
                emit("[FINAL RESULT] script action auto-executed (see logs)")
                _save_final_result("Script action auto-executed; see logs.")
            else:
                try:
                    if isinstance(script, str) and script.strip().startswith(
                            ("python", "import ", "print(", "def ", "from ", "for ", "if ",
                             "while ", "class ")):
                        # 执行脚本并捕获其输出，连同决策写入最终结果（避免最终结果只有 9 字被判过短）
                        import io as _io
                        import contextlib as _clb
                        _buf = _io.StringIO()
                        try:
                            with _clb.redirect_stdout(_buf):
                                exec(compile(script, "<decision>", "exec"),
                                     {"__name__": "__main__", "RESULT4": decision})
                            _exec_out = _buf.getvalue().strip()
                        except Exception as _e:
                            _exec_out = "[Script failed] " + str(_e)
                        _final_txt = "Script output:" + (_exec_out or "(no output)") + \
                                     "\nDecision:" + json.dumps(decision, ensure_ascii=False)
                        emit("[Decision] script executed")
                        emit("[Final Result] " + summary(_final_txt, 300))
                        _save_final_result(_final_txt)
                    elif isinstance(script, str) and (
                            re.match(r"^(?:[A-Za-z]:[\\/]|/|\\./|~/|[A-Za-z0-9_.-]+\\.(?:py|exe|bat|sh|cmd))",
                                      script.strip()) or re.search(r"[;&|>]", script)):
                        subprocess.run(str(script), shell=True,
                                       timeout=cfg.get("run_timeout", 120))
                        emit("[Decision] script executed")
                        emit("[FINAL RESULT] script action finished")
                        _save_final_result("Script action finished.")
                    else:
                        # 不是代码也不是可执行命令/路径：视为文字消息，不执行
                        _msg_txt = str(script).strip()
                        emit("[Decision] " + _msg_txt)
                        emit("[Final Result] " + _msg_txt)
                        _save_final_result("Text result:" + _msg_txt)
                except Exception as e:
                    emit(f"[Decision] script failed: {e}")
            return
    emit("[DECISION] decision content: " + summary(json.dumps(decision, ensure_ascii=False), 500))


def run_program3(cfg, emit=print, popup=False, extra_env=None):
    """运行 subfolder/run_program.py，捕获输出并处理 JSON 决策。返回退出码。"""
    folder = os.path.dirname(os.path.abspath(__file__))
    sub_dir = os.path.join(folder, "subfolder")
    mem_dir = os.path.join(folder, "Memory")
    py3 = os.path.join(sub_dir, "run_program.py")
    emit("[MAIN] running Program3...")
    env = dict(os.environ)
    env["MAIN_RESULT4"] = ""
    if not popup:
        env["P3_NO_GUI"] = "1"   # 无头/命令行模式：静默运行程序3（输出走控制台/日志）
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.Popen([sys.executable, py3], cwd=sub_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env)
        CURRENT_PROC[0] = proc
        try:
            # 轮询等待退出（避免子进程继承管道导致 communicate 永久阻塞）
            _wait_n = 0
            while proc.poll() is None and _wait_n < cfg.get("run_timeout", 280):
                time.sleep(0.2); _wait_n += 0.2
            if proc.poll() is None:
                proc.kill()
                emit("[Program3] timeout; terminated")
            out_b, err_b = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_b, err_b = proc.communicate()
            emit("[MAIN] Program3 timeout; terminated")
        finally:
            CURRENT_PROC[0] = None
        out = (decode_bytes(out_b) or "") + \
              ("\n" + decode_bytes(err_b) if err_b else "")
        save_text(os.path.join(mem_dir, "run_output.txt"), out)
        emit("[MAIN] Program3 exit code: " + str(proc.returncode))
        emit(out[-4000:] if out.strip() else "(no output)")
        handle_decision(out, cfg, emit=emit, popup=popup)
        return proc.returncode
    except Exception as e:
        emit(f"[MAIN] failed to run Program3: {e}")
        return -1


# 程序3 内置窗口壳：可输入可输出，与 AI 生成代码对接。
# - window_out(text)：AI 代码输出（GUI 显示到窗口；静默模式走 print）
# - window_in(prompt)：AI 代码请求用户输入一般信息（GUI 弹输入框等待提交；静默模式走 input）
# - window_inputs()：AI 代码读取用户填写的登录凭据（账号/密码/授权码 等输入框，
#   填给程序自己用，不发回 LLM；GUI 下会阻塞等待用户点「提交信息」；静默模式返回空串）
# - print 自动重定向到窗口；input 自动重定向到窗口输入框
# - P3_NO_GUI=1（程序2 内部自动运行）时静默执行，不弹窗口
WINDOW_SHELL_SOURCE = (
    "# ---- Program3 built-in window (input+output, wired to AI code) ----\n"
    "import queue as _que\n"
    "import contextlib as _clb\n"
    "import builtins as _bts\n"
    "import sys as _sys\n"
    "import ast as _ast\n"
    "import os as _os\n"
    "_orig_out = _sys.stdout\n"
    "_io = {'out_q': _que.Queue(), 'in_evt': threading.Event(), 'in_val': [None],\n"
    "       'gui': None, 'done': [False], 'fields': [], 'labels': []}\n"
    "\n"
    "def window_out(text):\n"
    "    s = str(text)\n"
    "    if _io['gui'] is not None:\n"
    "        _io['out_q'].put(s)\n"
    "    try:\n"
    "        print(s, file=_orig_out)\n"
    "    except Exception:\n"
    "        print(s)\n"
    "\n"
    "def window_in(prompt='Please enter:'):\n"
    "    if _io['gui'] is None:\n"
    "        # silent mode (run inside Program2): must not block waiting for input; auto-return empty string to avoid hang/timeout\n"
    "        try:\n"
    "            _sys.stderr.write('[Program3-silent] AI requested input; auto-returned empty string: ' + str(prompt) + '\\n')\n"
    "        except Exception:\n"
    "            pass\n"
    "        return ''\n"
    "    _io['in_val'][0] = None\n"
    "    _io['in_evt'].clear()\n"
    "    _io['out_q'].put('\\n[Input needed] ' + str(prompt) + '(fill in the input box below and click \"Submit\")')\n"
    "    while _io['in_val'][0] is None:\n"
    "        if _io['done'][0]:\n"
    "            return ''\n"
    "        _io['in_evt'].wait(timeout=0.2)\n"
    "    return _io['in_val'][0]\n"
    "\n"
    "def _load_required_inputs():\n"
    "    # 从 AI 代码顶层提取 REQUIRED_INPUTS = [\"标签1\", ...] (max 5, plain string list); returns [] if none\n"
    "    try:\n"
    "        _tree = _ast.parse(_BLOCKS)\n"
    "        for _node in _tree.body:\n"
    "            if isinstance(_node, _ast.Assign) and any(\n"
    "                    isinstance(_t, _ast.Name) and _t.id == 'REQUIRED_INPUTS' for _t in _node.targets):\n"
    "                _v = _ast.literal_eval(_node.value)\n"
    "                if isinstance(_v, list):\n"
    "                    return [str(x) for x in _v][:5]\n"
    "    except Exception:\n"
    "        pass\n"
    "    return []\n"
    "\n"
    "def window_inputs():\n"
    "    # 读取用户在窗口参数输入框填写的值（给程序自己用，不发回 LLM）\n"
    "    if _io['gui'] is None or not _io['labels']:\n"
    "        return {}\n"
    "    _io['out_q'].put('\\n[Params needed] fill in the parameter inputs below (labels defined by the program), then click \"Submit params\".'\n"
    "                     'These parameters are for the program only.')\n"
    "    _io['in_val'][0] = None\n"
    "    _io['in_evt'].clear()\n"
    "    while _io['in_val'][0] is None:\n"
    "        if _io['done'][0]:\n"
    "            return {}\n"
    "        _io['in_evt'].wait(timeout=0.2)\n"
    "    d = {}\n"
    "    for _l, _v in _io['fields']:\n"
    "        d[_l] = _v.get().strip()\n"
    "    return d\n"
    "\n"
    "def _run_ai_code():\n"
    "    _old_in = _bts.input\n"
    "    _bts.input = window_in\n"
    "    try:\n"
    "        class _OutProxy:\n"
    "            def write(self, s):\n"
    "                if s and s.strip():\n"
    "                    window_out(s.rstrip('\\n'))\n"
    "            def flush(self):\n"
    "                pass\n"
    "        _g = dict(globals())\n"
    "        _g.update({'__name__': '__main__', 'RESULT4': RESULT4,\n"
    "                   'window_out': window_out, 'window_in': window_in,\n"
    "                   'window_inputs': window_inputs})\n"
    "        with _clb.redirect_stdout(_OutProxy()):\n"
    "            exec(compile(_BLOCKS, '<ai_code>', 'exec'), _g)\n"
    "        # 窗口兜底显示最终结果：无论 AI 代码是否 print，都把 Memory/final_result.txt 内容显示到窗口\n"
    "        try:\n"
    "            _res_f = _os.path.join(_os.getcwd(), 'Memory', 'final_result.txt')\n"
    "            if _os.path.exists(_res_f):\n"
    "                with open(_res_f, 'r', encoding='utf-8-sig', errors='ignore') as _rf:\n"
    "                    _res_txt = _rf.read().strip()\n"
    "                if _res_txt:\n"
    "                    window_out('[Final Result]')\n"
    "                    window_out(_res_txt[:2000])\n"
    "        except Exception:\n"
    "            pass\n"
    "        window_out('[Finished]')\n"
    "    except Exception as e:\n"
    "        window_out('[Failed]' + str(e))\n"
    "        # 输出标准失败决策 JSON：程序2 可识别（AI_EXEC_FAIL）并给出明确失败反馈\n"
    "        try:\n"
    "            window_out(json.dumps({'decision': {'message': 'AI_EXEC_FAIL', 'error': str(e)}}, ensure_ascii=False))\n"
    "        except Exception:\n"
    "            pass\n"
    "    finally:\n"
    "        _io['done'][0] = True\n"
    "        _io['in_evt'].set()\n"
    "        try:\n"
    "            _bts.input = _old_in\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "if os.environ.get('P3_NO_GUI') == '1':\n"
    "    _run_ai_code()   # 静默模式：print/input 走控制台（程序2 内部自动运行用）\n"
    "else:\n"
    "    try:\n"
    "        import ctypes as _ct\n"
    "        try:\n"
    "            _ct.CDLL('libX11.so.6').XInitThreads()\n"
    "        except Exception:\n"
    "            pass\n"
    "        import tkinter as _tk\n"
    "        _io['gui'] = True\n"
    "        _root = _tk.Tk()\n"
    "        _root.title('Program3 · Run Window (auto-run; fill below when input is needed)')\n"
    "        _root.geometry('820x700')\n"
    "        _out_text = _tk.Text(_root, wrap='word', font=('Consolas', 10))\n"
    "        _sb = _tk.Scrollbar(_root, command=_out_text.yview)\n"
    "        _out_text.config(yscrollcommand=_sb.set)\n"
    "        _sb.pack(side='right', fill='y')\n"
    "        _out_text.pack(fill='both', expand=True, padx=8, pady=(8, 4))\n"
    "        # 参数输入区：仅当 AI 代码顶层声明了 REQUIRED_INPUTS 标签时渲染；未声明则窗口只显示运行状况\n"
    "        _labels = _load_required_inputs()\n"
    "        _io['labels'] = _labels\n"
    "        if _labels:\n"
    "            _param_frm = _tk.LabelFrame(_root, text='Parameter input (labels defined by AI code; click \"Submit\" after filling, used by program only)')\n"
    "            _param_frm.pack(fill='x', padx=8, pady=2)\n"
    "            for _lbl in _labels:\n"
    "                _r = _tk.Frame(_param_frm)\n"
    "                _r.pack(fill='x', padx=6, pady=1)\n"
    "                _tk.Label(_r, text=_lbl, width=14, anchor='w').pack(side='left')\n"
    "                _v = _tk.StringVar()\n"
    "                _tk.Entry(_r, textvariable=_v, font=('Consolas', 10)).pack(side='left', fill='x', expand=True)\n"
    "                _io['fields'].append((_lbl, _v))\n"
    "            def _submit_params():\n"
    "                _io['in_val'][0] = True\n"
    "                _io['in_evt'].set()\n"
    "                _out_text.insert('end', '[Params submitted to program]\\n')\n"
    "                _out_text.see('end')\n"
    "            _tk.Button(_param_frm, text='Submit params', command=_submit_params).pack(pady=2)\n"
    "        # 通用输入区：window_in 用（AI 问一般问题时输入）\n"
    "        _in_row = _tk.Frame(_root)\n"
    "        _in_row.pack(fill='x', padx=8, pady=4)\n"
    "        _tk.Label(_in_row, text='Input:').pack(side='left')\n"
    "        _in_var = _tk.StringVar()\n"
    "        _in_entry = _tk.Entry(_in_row, textvariable=_in_var, font=('Consolas', 11))\n"
    "        _in_entry.pack(side='left', fill='x', expand=True, padx=4)\n"
    "        def _submit():\n"
    "            _io['in_val'][0] = _in_var.get().strip()\n"
    "            _io['in_evt'].set()\n"
    "            _in_var.set('')\n"
    "        _in_entry.bind('<Return>', lambda e: _submit())\n"
    "        _tk.Button(_in_row, text='Submit', command=_submit).pack(side='left')\n"
    "        def _close():\n"
    "            _io['done'][0] = True\n"
    "            _io['in_evt'].set()\n"
    "            _root.destroy()\n"
    "        _root.protocol('WM_DELETE_WINDOW', _close)\n"
    "        def _flush():\n"
    "            try:\n"
    "                while True:\n"
    "                    t = _io['out_q'].get_nowait()\n"
    "                    _out_text.insert('end', str(t) + '\\n')\n"
    "                    _out_text.see('end')\n"
    "            except Exception:\n"
    "                pass\n"
    "            if _io['done'][0]:\n"
    "                # 运行完毕：自动关闭窗口（无需用户点击），程序自动结束\n"
    "                _root.after(800, _root.destroy)\n"
    "            else:\n"
    "                _root.after(120, _flush)\n"
    "        _root.after(120, _flush)\n"
    "        threading.Thread(target=_run_ai_code, daemon=True).start()\n"
    "        _root.mainloop()\n"
    "        print('[Program3 finished]')\n"
    "    except Exception as e:\n"
    "        _io['gui'] = None\n"
    "        print('Window startup failed (no GUI), falling back to CLI mode: ' + str(e))\n"
    "        _run_ai_code()\n"
)



def apply_modern_style(root):
    """给 tkinter 界面应用现代配色风格（程序2：深蓝色主题）。"""
    from tkinter import ttk
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    BG = "#e8eef7"
    FG = "#0f172a"
    ACCENT = "#1e40af"
    style.configure(".", background=BG, foreground=FG, font=("", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TLabelframe", background=BG, foreground=ACCENT, font=("", 10, "bold"))
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
    style.configure("TButton", background="#c7d2fe", foreground=FG, padding=(10, 4), font=("", 9))
    style.map("TButton",
              background=[("active", "#a5b4fc"), ("pressed", ACCENT)],
              foreground=[("pressed", "#ffffff")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", padding=(12, 5))
    style.map("Accent.TButton",
              background=[("active", "#1e3a8a"), ("pressed", "#172554")])
    style.configure("TEntry", fieldbackground="#ffffff", foreground=FG)
    style.configure("TCombobox", fieldbackground="#ffffff", foreground=FG)
    root.configure(bg=BG)
    return style


def build_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    cfg = load_local_config()
    log_queue = queue.Queue()
    state = {"running": False}
    folder = os.path.dirname(os.path.abspath(__file__))

    root = tk.Tk()
    root.title("Main Program (Program2) · LLM connect + recognize to generate Program3 · v12")
    root.resizable(True, True)   # 允许自由缩放：拖动窗口边缘可等比调整，日志区自动占满剩余空间
    _sw = root.winfo_screenwidth()
    _sh = root.winfo_screenheight()
    _w = min(840, max(680, (_sw or 9999) - 160))
    _h = min(560, max(440, (_sh or 9999) - 200))   # 唤醒即小窗口；布局紧凑，底部运行栏默认可见；可再拖大/最大化
    root.geometry("%dx%d+%d+%d" % (_w, _h, max(0, (_sw - _w) // 2), max(0, (_sh - _h) // 2)))
    root.minsize(640, 420)
    apply_modern_style(root)

    ttk.Label(root, text="Task: " + TASK, font=("", 12, "bold")).grid(
        row=0, column=0, sticky="w", padx=14, pady=(12, 4))

    cfg_frame = ttk.LabelFrame(root, text="LLM Config (OpenAI-compatible API)")
    cfg_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 0))
    cfg_frame.columnconfigure(1, weight=2)
    cfg_frame.columnconfigure(3, weight=1)

    preset_var = tk.StringVar(value="Volcano Ark / Doubao")
    note_var = tk.StringVar(value=PLATFORM_PRESETS["Volcano Ark / Doubao"]["note"])

    v_base = tk.StringVar(value=cfg["base_url"])
    v_key = tk.StringVar(value="")
    v_model = tk.StringVar(value=cfg["model"])
    v_timeout = tk.StringVar(value=str(cfg["timeout"]))
    v_run_timeout = tk.StringVar(value=str(cfg["run_timeout"]))
    v_web = tk.BooleanVar(value=bool(cfg.get("web_search")))

    preset_combo = ttk.Combobox(cfg_frame, textvariable=preset_var,
                                values=list(PLATFORM_PRESETS.keys()), state="readonly")
    ttk.Label(cfg_frame, text="Platform preset:").grid(row=0, column=0, sticky="w", padx=8, pady=2)
    preset_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=2)

    def on_preset(event=None):
        p = PLATFORM_PRESETS.get(preset_var.get())
        if not p:
            return
        if event is not None:  # 仅用户手动切换预设时覆盖地址/模型；初始调用只联动模型列表
            v_base.set(p["base_url"])
            v_model.set(p["model"])
            note_var.set(p["note"])
        # 模型下拉框随平台联动：只显示该平台正确的模型名，避免选到格式不对的模型
        model_combo["values"] = MODELS_BY_PLATFORM.get(preset_var.get(), COMMON_MODELS)

    preset_combo.bind("<<ComboboxSelected>>", on_preset)

    ttk.Label(cfg_frame, text="API base_url:").grid(row=1, column=0, sticky="w", padx=8, pady=2)
    v_base_combo = ttk.Combobox(cfg_frame, textvariable=v_base)
    v_base_combo.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=2)
    ttk.Label(cfg_frame, text="API Key:").grid(row=2, column=0, sticky="w", padx=8, pady=2)
    ttk.Entry(cfg_frame, textvariable=v_key).grid(row=2, column=1, columnspan=2, sticky="ew", padx=8, pady=2)
    ttk.Label(cfg_frame, text="Model:").grid(row=3, column=0, sticky="w", padx=8, pady=2)
    model_combo = ttk.Combobox(cfg_frame, textvariable=v_model, values=COMMON_MODELS)
    model_combo.grid(row=3, column=1, sticky="ew", padx=8, pady=2)
    ttk.Label(cfg_frame, text="Call timeout (sec):").grid(row=3, column=2, sticky="w", padx=8, pady=2)
    v_timeout_entry = ttk.Entry(cfg_frame, textvariable=v_timeout, width=8)
    v_timeout_entry.grid(row=3, column=3, sticky="ew", padx=8, pady=2)
    on_preset()
    ttk.Label(cfg_frame, text="Run timeout (sec):").grid(row=4, column=0, sticky="w", padx=8, pady=2)
    v_run_entry = ttk.Entry(cfg_frame, textvariable=v_run_timeout, width=8)
    v_run_entry.grid(row=4, column=1, sticky="w", padx=8, pady=2)
    ttk.Label(cfg_frame, textvariable=note_var, foreground="#8a5a00", wraplength=600,
              justify="left").grid(row=4, column=2, columnspan=2, sticky="w", padx=8, pady=(2, 6))
    ttk.Label(cfg_frame, text="Input mode:").grid(row=5, column=0, sticky="w", padx=8, pady=2)
    input_mode_var = tk.StringVar(value=cfg.get("input_mode", "window3"))
    input_mode_combo = ttk.Combobox(cfg_frame, textvariable=input_mode_var, state="readonly",
                                     values=["window3", "window2"])
    input_mode_combo.grid(row=5, column=1, sticky="w", padx=8, pady=2)
    ttk.Checkbutton(cfg_frame, text="Web search (requires model support: Doubao / DeepSeek R1)",
                    variable=v_web).grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 2))
    ttk.Label(cfg_frame, foreground="#5a5a5a", wraplength=640, justify="left",
              text="window3= input in Program3 window (skip Program2 collection, generate & run Program3 directly);"
                   "window2= input in Program2 window (run Program2 collection, pause for you to fill the info box)"              ).grid(row=5, column=2, columnspan=2, sticky="w", padx=8, pady=(2, 6))

    def read_cfg_from_gui():
        d = {
            "base_url": v_base.get().strip(),
            "api_key": v_key.get().strip(),
            "model": normalize_model_name(v_model.get(), v_base.get()),
            "temperature": 0.2,
            "timeout": int(v_timeout.get() or 280),
            "run_timeout": int(v_run_timeout.get() or 280),
            "input_mode": input_mode_var.get() or "window3",
            "web_search": bool(v_web.get()),
        }
        # 界面未填 Key 时，回填本地密钥文件 / 训练时内嵌密钥；界面始终不显示
        if not d.get("api_key"):
            d["api_key"] = CONFIG.get("api_key", "")
        if not d.get("api_key"):
            d["api_key"] = _load_persisted_key()
        return d

    btns = ttk.Frame(root)
    btns.grid(row=2, column=0, sticky="w", padx=14, pady=6)

    # ---- 采集方式提示（本次任务信息采集方式）----
    collect_mode_var = tk.StringVar(value="")
    ttk.Label(btns, textvariable=collect_mode_var, font=("", 10, "bold"),
              foreground="#1e40af").grid(row=0, column=0, sticky="w", padx=4, pady=(0, 2))
    collect_mode_var.set("Collection: LLM-based (fully by LLM; click 'Collect info via LLM & compute')")

    llm_run_btn = ttk.Button(btns, text="Collect info via LLM & compute")
    llm_chat_btn = ttk.Button(btns, text="Send info to LLM")
    stop_btn = ttk.Button(btns, text="Stop")

    # ---- 信息输入区（单框：需要提交给 LLM 的信息都填在这里）----
    env_frame = ttk.LabelFrame(root, text="Info input (fill info to submit to AI here, e.g. account/password/token, one per line)")
    env_frame.grid(row=3, column=0, sticky="ew", padx=14, pady=(6, 0))
    env_box = tk.Text(env_frame, height=2, wrap="word", font=("Consolas", 10))
    env_box.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
    _env_gui = read_env_info()
    if _env_gui:
        env_box.insert("1.0", _env_gui)
    env_sb = ttk.Scrollbar(env_frame, orient="vertical", command=env_box.yview)
    env_sb.grid(row=0, column=1, sticky="ns")
    env_box.config(yscrollcommand=env_sb.set)
    env_frame.columnconfigure(0, weight=1)

    # ---- LLM 输出区（只读：显示 LLM 返回的所有信息）----
    input_frame = ttk.LabelFrame(root, text="LLM output (all AI replies shown here)")
    input_frame.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 0))
    user_input_answer = {"value": None}  # 用 dict 存，方便闭包修改
    user_input_event = threading.Event()
    waiting_input = {"flag": False}  # 运行中是否正等待用户补充信息（提交后解除等待）

    def set_user_answer(val):
        user_input_answer["value"] = val
        user_input_event.set()

    text_box = tk.Text(input_frame, height=4, wrap="word", font=("Consolas", 10), state="disabled")
    text_box.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
    text_sb = ttk.Scrollbar(input_frame, orient="vertical", command=text_box.yview)
    text_sb.grid(row=0, column=2, sticky="ns")
    text_box.config(yscrollcommand=text_sb.set)
    text_box.config(state="normal")
    text_box.insert("1.0", "(all LLM replies will be shown here)")
    text_box.config(state="disabled")
    ttk.Label(input_frame,
              text="Note: read-only output area showing all LLM replies; to submit info to LLM,"
                   "fill the info box and click 'Send info to LLM'.",
              foreground="#5a5a5a", wraplength=740, justify="left").grid(
        row=1, column=0, sticky="w", padx=8, pady=(0, 6))
    input_frame.columnconfigure(0, weight=1)

    def ask_user(question):
        """暂停等待用户输入：显示问题；等待用户在「信息输入」框填写信息后
        点击「向 LLM 输入信息」按钮提交给 LLM，提交后流程继续。期间可被「停止」按钮打断。"""
        log_queue.put(f"[Input needed] {question}")
        log_queue.put("[Input needed] fill in the Info box (server/account/token etc., one per line),"
                      "then click 'Send info to LLM' to submit...")
        user_input_event.clear()
        user_input_answer["value"] = None
        waiting_input["flag"] = True
        # 「向 LLM 输入信息」按钮始终可用：等待补充信息期间直接点击提交
        while user_input_answer["value"] is None and not STOP_REQUESTED[0]:
            user_input_event.wait(timeout=1)
        waiting_input["flag"] = False
        if STOP_REQUESTED[0]:
            log_queue.put("[STOP] Waiting for user input to be interrupted by Stop button")
            return ""
        answer = user_input_answer["value"] or ""
        if answer:
            # 提交后把最新环境信息落盘（采集/程序3 从中读取）
            save_env_info(env_box.get("1.0", "end").strip())
        log_queue.put(f"[Input needed] submitted: {summary(answer, 100)}")
        return answer

    def emit(msg):
        log_queue.put(str(msg))

    def _prepare_cfg():
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("Info", f"Invalid number format in config: {e}")
            return None
        # 界面未填 Key 时，回填本地密钥文件 / 训练时内嵌密钥
        if not new_cfg.get("api_key"):
            new_cfg["api_key"] = CONFIG.get("api_key", "")
        if not new_cfg.get("api_key"):
            new_cfg["api_key"] = _load_persisted_key()
        if new_cfg.get("api_key"):
            persist_key(new_cfg)
        save_local_config(new_cfg)
        # 保存信息输入框内容 → 环境信息.json（采集/程序3 从中读取）
        save_env_info(env_box.get("1.0", "end").strip())
        return new_cfg

    def _start_work(status_text, worker, clear_log=True):
        """Unified entry: validate config → lock buttons → run in background thread."""
        if state["running"]:
            return False
        new_cfg = _prepare_cfg()
        if new_cfg is None:
            return False
        state["running"] = True
        llm_run_btn.config(state="disabled")
        stop_btn.config(state="normal")
        if clear_log:
            log_list.delete(0, "end")
        status_var.set(status_text)

        def run():
            try:
                worker(new_cfg)
            except Exception as e:
                emit(f"[MAIN] Failed: {e}")
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=run, daemon=True).start()
        return True

    def on_llm_run():
        """Collect via LLM: force LLM collection → format → generate Program3 → run"""
        _start_work("Running (LLM collect → format → generate Program3 → run)...",
                    lambda c: run_auto(c, emit=emit, popup=True, ask_user=ask_user, force_mode="llm"))


    def on_auto():
        """Auto: LLM collect (reuse converted result if available) → generate Program3 → run"""
        _start_work("Auto running (LLM collect → generate Program3 → run)...",
                    lambda c: run_auto(c, emit=emit, popup=True, ask_user=ask_user, force_mode=None))

    def on_llm_chat():
        """向 LLM 输入信息：提交「信息输入」框内容给 LLM，回复显示在 LLM 输出区。
        任何时候都能提交（包括程序运行中）；若正等待用户补充信息（AI 判断需要），
        提交后解除等待、主流程继续。"""
        env_text = env_box.get("1.0", "end").strip()
        if not env_text:
            messagebox.showwarning("Info", "Please fill the info to send to LLM in the info box")
            return
        new_cfg = _prepare_cfg()
        if new_cfg is None:
            return
        was_running = state["running"]
        if not was_running:
            state["running"] = True
            llm_run_btn.config(state="disabled")
            stop_btn.config(state="normal")
        status_var.set("Sending to LLM...")
        content = "User-provided info (please continue accordingly):\n" + env_text
        emit(f"[DEBUG] sent to LLM: {summary(content, 200)}")

        def work():
            _was_waiting = waiting_input["flag"]
            try:
                reply = llm_ask([{"role": "user", "content": content}], new_cfg)
                # 跨线程不能直接操作 Tk 控件：回复经队列交给主线程 poll() 显示到 LLM 输出区
                log_queue.put(("_chat_reply", reply))
                if _was_waiting:
                    # 等待补充信息场景：提交给 LLM 后解除等待，主流程继续（不解除运行状态）
                    waiting_input["flag"] = False
                    set_user_answer("Environment info submitted to LLM (see AI reply in output area); main flow continues")
                elif not was_running:
                    log_queue.put(("_done", None))
                # 运行中提交：只显示回复，不打扰主流程
            except Exception as e:
                emit(f"[DEBUG] send failed: {e}")
                if _was_waiting:
                    emit("[ENV] submit failed: check the LLM config and retry (flow still waiting for submit)")
                elif not was_running:
                    log_queue.put(("_done", None))
            finally:
                _flush_dialog_log()  # 人工调试的发送+回复也写入 llm_chat_log.txt

        threading.Thread(target=work, daemon=True).start()

    def on_stop():
        """Stop button: request to stop current flow and terminate running subprocess (collect script / Program3)"""
        STOP_REQUESTED[0] = True
        proc = CURRENT_PROC[0]
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception as _e:
                pass
            emit("[STOP] Subprocess terminated; flow will exit next")
        else:
            emit("[STOP] Stop requested (no running subprocess; waiting for flow exit)")

    llm_run_btn.config(command=on_llm_run)
    llm_run_btn.grid(row=1, column=0, padx=4)
    llm_chat_btn.config(command=on_llm_chat)
    llm_chat_btn.grid(row=1, column=1, padx=4)
    stop_btn.config(command=on_stop, state="disabled")
    stop_btn.grid(row=1, column=2, padx=4)

    log_list = tk.Listbox(root, height=7, font=("Courier", 9),
                          exportselection=False, activestyle="none")
    ysb = ttk.Scrollbar(root, orient="vertical", command=log_list.yview)
    xsb = ttk.Scrollbar(root, orient="horizontal", command=log_list.xview)
    log_list.config(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    log_list.grid(row=5, column=0, sticky="nsew", padx=(14, 0))
    ysb.grid(row=5, column=1, sticky="ns")
    xsb.grid(row=6, column=0, sticky="ew", padx=14)

    status_var = tk.StringVar(value="Ready")
    ttk.Label(root, textvariable=status_var, anchor="w").grid(
        row=7, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 6))

    root.columnconfigure(0, weight=1)
    root.rowconfigure(5, weight=1)
    cfg_frame.columnconfigure(1, weight=2)
    cfg_frame.columnconfigure(3, weight=1)

    def poll():
        try:
            while True:
                item = log_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "_chat_reply":
                    # 主线程把 LLM 回复显示到 LLM 输出区（只读框，插入前临时启用）
                    reply = item[1]
                    text_box.config(state="normal")
                    text_box.insert("end", "\n\n===== LLM reply =====\n" + reply)
                    text_box.see("end")
                    text_box.config(state="disabled")
                    log_list.insert("end", "[DEBUG] LLM reply shown in the LLM output area")
                    continue
                if isinstance(item, tuple) and item[0] == "_done":
                    state["running"] = False
                    llm_run_btn.config(state="normal")
                    llm_chat_btn.config(state="normal")
                    stop_btn.config(state="disabled")
                    status_var.set("Finished")
                    continue
                if isinstance(item, tuple) and item[0] == "_feedback":
                    # 全自动：文字反馈直接记日志；脚本反馈后台线程执行，不弹窗等待点击
                    kind, payload = item[1], item[2]
                    if kind == "text":
                        log_list.insert("end", "[Feedback] text: " + str(payload))
                    else:
                        def _exec_fb(code=payload):
                            try:
                                exec(compile(code, "<feedback_script>", "exec"),
                                     {"__name__": "__main__"})
                                log_list.insert("end", "[Feedback] script executed")
                            except Exception as e:
                                log_list.insert("end", f"[Feedback] script failed: {e}")
                        threading.Thread(target=_exec_fb, daemon=True).start()
                    continue
                for line in str(item).splitlines() or [""]:
                    log_list.insert("end", line)
                log_list.see("end")
        except queue.Empty:
            pass
        root.after(100, poll)

    root.after(100, poll)

    # 布局完成后按内容需求高度自适应窗口，保证底部运行栏（日志区）默认可见，无需手动拉伸
    def _fit_window():
        try:
            root.update_idletasks()
            need = root.winfo_reqheight()
            scr = root.winfo_screenheight() or 768
            if need > 0:
                root.geometry("%dx%d" % (root.winfo_width(), min(need + 6, scr - 80)))
        except Exception:
            pass
    root.after(60, _fit_window)

    def auto_start():
        if state["running"]:
            return
        on_auto()
        log_list.insert(0, "[AUTO] Program2 opened; auto-start (auto collect per plan → recognize → generate Program3 → run)")
        log_list.see(0)

    # 打开程序2 后自动工作（约 1.5 秒后启动完整流程）
    root.after(1500, auto_start)
    return root


def main():
    ap = argparse.ArgumentParser(
        description="Main program (Program2): LLM recognize → RESULT4 → generate & run Program3")
    ap.add_argument("--auto", action="store_true", help="CLI auto-execute (no GUI)")
    args = ap.parse_args()

    cfg = load_local_config()
    if args.auto:
        run_auto(cfg)
        return
    root = build_gui()
    root.mainloop()


if __name__ == "__main__":
    main()
'''


def generate_main_program(cfg, task, r0, r1, r_collect_plan, r2, r3, r6, mock):
    """程序1 自动生成 程序2（主程序.py）的源码。"""
    src = (MAIN_PROGRAM_TEMPLATE
           .replace("@@TASK@@", task.replace("'''", "'"))
           .replace("@@TASK_REPR@@", repr(task))
           .replace("@@CONFIG@@", repr(cfg))
           .replace("@@BATCH_UTIL@@", repr(BATCH_UTIL_SOURCE))
           .replace("@@P3_CONTRACT@@", repr(P3_CONTRACT_TEXT))
           .replace("@@BUILTIN_URLS@@", repr(BUILTIN_DATA_URLS))
           .replace("@@RESULT0@@", repr(r0))
           .replace("@@RESULT1@@", repr(r1))
           .replace("@@COLLECT_PLAN@@", repr(r_collect_plan))
           .replace("@@RESULT2@@", repr(r2))
           .replace("@@RESULT3@@", repr(r3))
           .replace("@@RESULT4W@@", repr(""))
           .replace("@@RESULT6@@", repr(r6))
           .replace("@@MOCK4@@", repr(MOCK_RESULT4) if mock else '""'))
    return src


def rebuild_main_from_last(cfg, emit=print, open_ui=False):
    """用最近一次训练结果快速重建 主程序.py（不调用 LLM，0 token）。
    适用场景：训练程序.py 模板/程序2 界面更新后，无需重新训练即可生成新版程序2。"""
    cur = os.path.join(BASE_DIR, "task_output", "current_task.txt")
    if not os.path.exists(cur):
        emit("[重建] 未找到 任务输出/当前任务.txt：请先完成一次训练")
        return None
    with open(cur, "r", encoding="utf-8-sig") as f:
        root_dir = f.read().strip()
    if not os.path.isdir(root_dir):
        emit(f"[重建] 最近任务文件夹不存在: {root_dir}")
        return None
    mem_dir = os.path.join(root_dir, "Memory")

    def read(name):
        p = os.path.join(mem_dir, name)
        if not os.path.exists(p):
            return ""
        with open(p, "r", encoding="utf-8-sig") as f:
            return f.read()

    task = read("task_desc.txt")
    r0, r1 = read("result0.txt"), read("result1.txt")
    plan = read("collect_plan.txt")
    r2, r3, r6 = read("result2.txt"), read("result3.txt"), read("result6.txt")
    if not (r1 and plan and r2 and r3):
        emit("[重建] 上次训练结果不完整（缺 结果1/收集方案/结果2/结果3），请重新训练")
        return None
    new_cfg = dict(cfg)
    if not new_cfg.get("api_key"):
        kp = os.path.join(mem_dir, "api_key.txt")
        if os.path.exists(kp):
            with open(kp, "r", encoding="utf-8") as f:
                new_cfg["api_key"] = f.read().strip()
    emit("[重建] 使用上次训练结果快速重建 主程序.py（不调用 LLM，0 token）...")
    main_src = generate_main_program(new_cfg, task, r0, r1, plan, r2, r3, r6, mock=False)
    try:
        compile(main_src, "<main>", "exec")
    except SyntaxError as e:
        emit(f"[重建] 语法错误: {e}")
        return None
    main_path = os.path.join(root_dir, "main_program.py")
    with open(main_path, "w", encoding="utf-8-sig") as f:
        f.write(main_src)
    emit(f"[重建] 已生成新版 主程序.py（最新模板，0 token）: {main_path}")
    if open_ui:
        try:
            if sys.platform.startswith("win"):
                os.startfile(main_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", main_path])
            else:
                subprocess.Popen(["xdg-open", main_path])
        except Exception as e:
            emit(f"[重建] 打开程序2 失败: {e}")
    return main_path


# ---------------------------------------------------------------- 全流程（程序1）

def run_training(task, cfg, emit=print, mock=False, open_ui=False):
    """命令0-6 → 结果0/1/2/3/4(窗口代码)/6 → 建文件夹 → 生成 程序2 → 打开/运行程序2。"""
    STOP_REQUESTED[0] = False

    def check_stop():
        if STOP_REQUESTED[0]:
            emit("[STOP] Stop requested; training aborted")
            return True
        return False

    try:
        emit("=" * 64)
        emit(f"Task: {task}")
        emit(f"[TRAIN] Using LLM config → {cfg.get('base_url')} | model: {cfg.get('model')} | "
             f"Key: {('****'+cfg['api_key'][-4:]) if cfg.get('api_key') else '(empty)'}")
        # 每一步独立发送 Prompt，不累积对话历史，只粘贴必要的上下文

        # 独立步骤：程序开始之前，先向 LLM 单独发送「开启新对话」prompt
        emit("[NEW CHAT] Sending 'start new conversation' prompt to LLM first...")
        r_new_chat_1 = ask_llm(cfg, [{"role": "user", "content": "Start new conversation"}], mock, "new_chat")
        emit(f"  AI response: {summary(r_new_chat_1, 200)}")
        if check_stop():
            return None

        emit("[TRAIN 1/6] CMD0: plan how to solve the task with state/choice...")
        p0 = CMD0_TEMPLATE.format(user_task=task) + "\n\n" + TOOLS_REGISTRY
        emit(f"  [命令0] 已发送给 LLM，正在等待回复（最长 {cfg.get('timeout', 280)} 秒；若超时将自动重试一次）...")
        try:
            r0 = ask_llm(cfg, [{"role": "user", "content": p0}], mock, 0)
        except Exception as _e0:
            if mock:
                raise
            if "超时" not in str(_e0) and "timed out" not in str(_e0).lower():
                raise
            emit(f"  [自动重试] 命令0 请求超时（{summary(str(_e0), 120)}），"
                 f"30 秒后以较短超时（90 秒）自动重试 1 次 ...")
            time.sleep(30)
            cfg_retry = dict(cfg)
            cfg_retry["timeout"] = min(int(cfg.get("timeout", 280) or 280), 90)
            r0 = ask_llm(cfg_retry, [{"role": "user", "content": p0}], mock, 0)
            emit("  [自动重试] 第 2 次请求已发出，正在等待回复 ...")
        emit(f"  RESULT0(plan): {summary(r0, 300)}")
        if check_stop():
            return None

        emit("[TRAIN 2/6] CMD1: split the work into state/choice, output strict JSON definition...")
        p1 = CMD1_TEMPLATE.format(user_task=task)
        r1 = ask_llm(cfg, [{"role": "user", "content": p1}], mock, 1)
        try:
            json.loads(r1)
            emit(f"  RESULT1: {summary(r1)} (strict JSON definition, states/choices only)")
        except Exception:
            emit(f"  RESULT1: {summary(r1)}")
            emit("  [WARN] RESULT1 is not strict JSON (should only contain states/choices), retraining recommended")
        if check_stop():
            return None

        emit("[TRAIN 3/6] CMD2: design info collection plan (script or Prompt, decide per state)...")
        # 粘贴结果1 作为上下文 + 可用工具清单（脚本采集可用 batch_util 批量读取）
        prompt_collect = CMD_COLLECT_PLAN + "\n\n结果1（state 定义）：\n" + r1
        r_collect_plan = ask_llm(cfg, [{"role": "user", "content": prompt_collect}],
                                 mock, "collect_plan")
        emit(f"  Collect plan: {summary(r_collect_plan, 400)}")
        if check_stop():
            return None

        emit("[TRAIN 4/6] CMD3: generate state→choice computation program (compute from RESULT4)...")
        # 只粘贴结果1 作为上下文，不粘贴收集方案（运算逻辑不依赖采集方式）+ 批量任务约束
        prompt_cmd2 = CMD2 + "\n\n" + BATCH_GUIDANCE + "\n\nRESULT1 (state/choice definitions):\n" + r1
        r2 = ask_llm(cfg, [{"role": "user", "content": prompt_cmd2}], mock, 2)
        emit(f"  RESULT2(compute): {summary(r2)}")
        if check_stop():
            return None

        emit("[TRAIN 5/6] CMD4: write choice executor + window output code (merge into one Python program)...")
        # 只粘贴结果1 作为上下文，不粘贴结果2（执行程序只依赖 choice 定义与决策输出格式）+ 批量任务约束
        prompt_cmd3 = CMD3_EXEC + "\n\n" + P3_FRAMEWORK_HINT + "\n\n" + BATCH_GUIDANCE + "\n\nRESULT1 (state/choice definitions):\n" + r1
        r3 = ask_llm(cfg, [{"role": "user", "content": prompt_cmd3}], mock, 3)
        emit(f"  RESULT3(executor+window code): {summary(r3)}")
        if check_stop():
            return None

        emit("[TRAIN 6/6] CMD5: design verification criteria (process+output, no actual check)...")
        # 粘贴结果1 作为上下文
        prompt_cmd5 = CMD5 + "\n\nRESULT1 (state/choice definitions):\n" + r1
        r6 = ask_llm(cfg, [{"role": "user", "content": prompt_cmd5}], mock, 6)
        emit(f"  RESULT6(verification): {summary(r6, 300)}")
        if check_stop():
            return None

        # ---- 程序代码校验与自动修复（静态：工具白名单 + 语法；直到跑通或达上限）----
        max_fix = int(cfg.get("max_fix_rounds", 5))
        for fix_round in range(1, max_fix + 1):
            problems = []
            for _label, _code in (("RESULT2 (compute)", r2), ("RESULT3 (executor)", r3)):
                _ok, _errs = validate_code_imports(strip_code_fence(_code), _label)
                problems.extend(_errs)
            if not problems:
                break
            emit(f"[AUTO-FIX {fix_round}/{max_fix}] {len(problems)} code problems found; asking LLM to rewrite...")
            for _p in problems:
                emit(f"  - {_p}")
            fix_prompt = (
                "你之前为任务生成的程序代码存在以下问题（违反工具白名单或存在语法错误）：\n"
                + "\n".join("- " + _p for _p in problems) + "\n\n"
                "硬性要求：\n"
                "1. 只使用 Python 标准库与内置 batch_util 工具，禁止 import 任何第三方库\n"
                "2. Re-output TWO complete runnable Python programs: first [RESULT2: state→choice compute program] code block,"
                "再输出【结果3：choice 执行程序】代码块，均用 ```python 围栏\n"
                "Task definition (RESULT1):\n" + r1
            )
            combined = ask_llm(cfg, [{"role": "user", "content": fix_prompt}], mock, "fix_code")
            _fences = re.findall(r"```(?:python)?\s*(.*?)```", combined, re.S)
            if len(_fences) >= 2:
                r2, r3 = _fences[0], _fences[1]
                emit(f"[AUTO-FIX] LLM rewrote: RESULT2({len(r2)} chars) + RESULT3({len(r3)} chars)")
            else:
                emit("[AUTO-FIX] Could not parse the two code blocks; this round is ineffective, moving on")
            if check_stop():
                return None
        if not problems:
            emit("[CHECK] program code passed validation (whitelist + syntax)")
        else:
            emit("[CHECK] max fix rounds reached without passing static checks; generating main program anyway (runtime verifies further)")

        # 新建文件夹 + subfolder + Memory
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_root = os.path.join(BASE_DIR, "task_output")
        root_dir = os.path.join(out_root, f"task_{stamp}_{safe_task_name(task)}")
        sub_dir = os.path.join(root_dir, "subfolder")
        mem_dir = os.path.join(root_dir, "Memory")
        os.makedirs(sub_dir, exist_ok=True)
        os.makedirs(mem_dir, exist_ok=True)
        CURRENT_MEM_DIR[0] = mem_dir  # 记录钩子：此后每次 LLM 调用自动写盘对话记录
        with open(os.path.join(out_root, "current_task.txt"), "w", encoding="utf-8-sig") as f:
            f.write(root_dir)

        save_text(os.path.join(mem_dir, "task_desc.txt"), task)
        save_text(os.path.join(mem_dir, "result0.txt"), r0)
        save_text(os.path.join(mem_dir, "result1.txt"), r1)
        save_text(os.path.join(mem_dir, "collect_plan.txt"), r_collect_plan)
        save_text(os.path.join(mem_dir, "result2.txt"), r2)
        save_text(os.path.join(mem_dir, "result3.txt"), r3)
        save_text(os.path.join(mem_dir, "result6.txt"), r6)
        # 把本次训练的 Key 写入新任务密钥文件：程序2 打开后自动流程可直接使用，
        # 避免残留旧的 密钥.txt 导致"New Key works in test but auto flow still cannot connect"
        if cfg.get("api_key"):
            try:
                with open(os.path.join(mem_dir, "api_key.txt"), "w", encoding="utf-8") as f:
                    f.write(cfg["api_key"])
            except Exception:
                pass
        emit(f"[TRAIN DONE] Created folder: {root_dir}")
        emit(f"           subfolder: {sub_dir}")
        emit(f"           RESULT0-6 & collect plan saved to Memory/ (RESULT4=window output code)")

        # （已按要求移除：打开程序2 之前不再发送「开启新对话」，只有任务开始时发一次）

        # 生成 程序2（主程序.py）并运行验证（动态验证：失败自动让 LLM 修复后重跑，直到跑通或达上限）
        main_path = os.path.join(root_dir, "main_program.py")
        run_ok = False
        for run_round in range(1, max_fix + 1):
            emit(f"[P1→P2] Generating main_program.py (v{run_round}, embedding RESULT0-6, plan & config)...")
            main_src = generate_main_program(cfg, task, r0, r1, r_collect_plan,
                                             r2, r3, r6, mock)
            save_text(main_path, main_src)
            emit(f"[P1→P2] Generated: {main_path}")

            if open_ui and not mock:
                # 直接打开程序2 的可视化界面（不阻塞程序1，程序2 打开后自动工作）
                emit("[P1→P2] Opening main_program.py GUI (Program2)...")
                subprocess.Popen([sys.executable, main_path], cwd=root_dir)
                emit("[P2] Opened; will auto run: collect → recognize → generate Program3 → run.")
                run_ok = True
                break

            # 自检/后台模式：命令行自动运行程序2 → 它生成并运行程序3
            emit(f"[P2] Starting main_program.py --auto (round {run_round})...")
            env = dict(os.environ)
            if mock:
                env["MAIN_PROGRAM_MOCK"] = "1"
            out = ""
            rc = -1
            try:
                proc = subprocess.run([sys.executable, main_path, "--auto"], cwd=root_dir,
                                      capture_output=True,
                                      timeout=cfg.get("run_timeout", 280),
                                      env=env)
                rc = proc.returncode
                out = (decode_bytes(proc.stdout) or "") + \
                      ("\n" + decode_bytes(proc.stderr) if proc.stderr else "")
            except subprocess.TimeoutExpired as e:
                out = (decode_bytes(e.stdout or b"") or "") + "\n主程序运行超时"
            save_text(os.path.join(mem_dir, "main_output.txt"), out)
            emit(f"[P2] Exit code: {rc}")
            for line in out.strip().splitlines()[-40:]:
                emit(f"  | {line}")

            # 结果质量检查（真实训练时）：最终结果过短（只有一句文字）视为不达标，触发自动修复
            if not mock:
                _final_path = os.path.join(mem_dir, "final_result.txt")
                _final_text = ""
                try:
                    if os.path.exists(_final_path):
                        with open(_final_path, "r", encoding="utf-8-sig") as f:
                            _final_text = f.read().strip()
                except Exception:
                    pass
                _core = re.sub(r"^文字结果[:：]\s*", "", _final_text).strip()
                if not _core:
                    emit("[VERIFY] quality not met: no final result; triggering auto-fix")
                    out += "\n[结果质量不达标：未生成最终结果]"
                elif len(_core) < 15:
                    emit(f"[VERIFY] quality not met: final result too short ({len(_core)} chars); triggering auto-fix")
                    out += f"\n[结果质量不达标：最终结果过短] {_final_text}"

            # 判定是否跑通：无失败标志，且出现了流程完成标志
            fail_markers = ["[错误]", "Program3 has no output", "not parsed as JSON", "timeout", "Traceback",
                            "cannot connect", "validation failed", "Program3 not started", "Program3 timeout",
                            "result quality not met"]
            done_markers = ("[执行完毕]", "[最终结果]", "[决策]", "[完成]")
            if out and not any(_mk in out for _mk in fail_markers) and \
                    any(_mk in out for _mk in done_markers):
                run_ok = True
                emit(f"[VERIFY] round {run_round} passed; stopping iteration")
                break

            if run_round >= max_fix:
                emit("[VERIFY] max rounds reached; keeping the last version (fix manually via 'Send info to LLM')")
                break

            # 未跑通：收集运行输出与现场，让 LLM 重写 结果3（执行程序）
            emit(f"[AUTO-FIX {run_round}/{max_fix}] main program failed; collecting scene for LLM to rewrite RESULT3...")
            fix_prompt = (
                "你生成的主程序运行未跑通，以下是运行输出（末尾部分）：\n"
                + (out[-3000:] if out else "(no output)") + "\n\n"
                "Task definition (RESULT1):\n" + r1 + "\n\n"
                "请只重写【结果3：choice 执行程序】的完整 Python 代码（用 ```python 围栏）：\n"
                "1. 只使用 Python 标准库与内置 batch_util，禁止第三方库\n"
                "2. Full auto-run: no input(), popups, mainloop, or sleep waiting for user;"
                "参数（登录凭据/数量/筛选条件等）：代码顶层声明 REQUIRED_INPUTS = [\"标签\",...]（最多5个），"
                "用内置函数 window_inputs() 获取 {\"标签\": 值} 字典（给程序自己用，禁止发给 LLM）；"
                "其他用户输入用 window_in(\"提示语\")（程序3 窗口弹输入框）；"
                "输出用 print（会自动显示在程序3 窗口）\n"                "7. 需要登录/凭据/账号/服务器地址等时，从常量 ENV_INFO 读取（用户提供的信息），"
                "不要自己假设或让用户再次输入"
            )
            r3 = ask_llm(cfg, [{"role": "user", "content": fix_prompt}], mock, "fix_runtime")
            emit(f"[AUTO-FIX] LLM rewrote RESULT3 ({len(r3)} chars); regenerating main program and re-running...")
            if check_stop():
                return None

        if not run_ok and not (open_ui and not mock):
            emit("[NOTE] still not fully working after multiple rounds; artifacts kept; fix later via Program2 feedback")

        emit("")
        emit("[DONE] Training and generation complete")
        emit(f"产物目录: {root_dir}")
        flush_dialog_log(mem_dir, emit)
        CURRENT_MEM_DIR[0] = None
        return root_dir
    except Exception as e:
        try:
            flush_dialog_log(mem_dir, emit)  # 失败也尽量保留对话记录
        except Exception:
            pass
        CURRENT_MEM_DIR[0] = None
        emit(f"[FAILED] {e}")
        raise


# ---------------------------------------------------------------- 可视化界面

def apply_modern_style(root):
    """给 tkinter 界面应用现代配色风格（程序1：明黄色主题）。"""
    from tkinter import ttk
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    BG = "#fffbeb"
    FG = "#451a03"
    ACCENT = "#d97706"
    style.configure(".", background=BG, foreground=FG, font=("", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TLabelframe", background=BG, foreground=ACCENT, font=("", 10, "bold"))
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
    style.configure("TButton", background="#fde68a", foreground=FG, padding=(10, 4), font=("", 9))
    style.map("TButton",
              background=[("active", "#fcd34d"), ("pressed", ACCENT)],
              foreground=[("pressed", "#ffffff")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", padding=(12, 5))
    style.map("Accent.TButton",
              background=[("active", "#b45309"), ("pressed", "#92400e")])
    style.configure("TEntry", fieldbackground="#ffffff", foreground=FG)
    style.configure("TCombobox", fieldbackground="#ffffff", foreground=FG)
    root.configure(bg=BG)
    return style


def build_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    cfg = load_config()
    log_queue = queue.Queue()
    state = {"running": False}

    root = tk.Tk()
    root.title("Training Program (Program1) · LLM auto-generates Program2/Program3 · v12")
    root.resizable(True, True)   # 允许自由缩放
    _sw = root.winfo_screenwidth()
    _sh = root.winfo_screenheight()
    _w = min(980, max(760, (_sw or 9999) - 120))
    _h = min(680, max(520, (_sh or 9999) - 130))   # 扣除任务栏/标题栏，保证日志区可见
    root.geometry("%dx%d+%d+%d" % (_w, _h, max(0, (_sw - _w) // 2), max(0, (_sh - _h) // 2)))
    root.minsize(740, 500)
    apply_modern_style(root)

    ttk.Label(root, text="What task would you like to complete?", font=("", 14, "bold")).grid(
        row=0, column=0, sticky="w", padx=14, pady=(14, 4))
    task_var = tk.StringVar(value="e.g. capture 10 gold-related webpages (kitco.com/gold-price-today-usa, gold.org/goldhub/data/gold-prices, cn.investing.com/commodities/gold etc.), extract 5 indicators as states: Gold_Price, 24h_Change, USD_Index, Risk_Sentiment, Gold_ETF_Flow, synthesize into one decision: Buy_Full/Sell_Full/Buy_Half/Sell_Half/No_Trade, save the decision & basis to Memory/gold_decision_result.txt. Login credentials need not be in the task; fill them in the Environment Info input below")
    ttk.Combobox(root, textvariable=task_var, font=("", 12)).grid(
        row=1, column=0, sticky="ew", padx=14)

    cfg_frame = ttk.LabelFrame(root, text="LLM Config (OpenAI-compatible API)")
    cfg_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 0))

    preset_var = tk.StringVar(value="Volcano Ark / Doubao")
    note_var = tk.StringVar(value=PLATFORM_PRESETS["Volcano Ark / Doubao"]["note"])

    v_base = tk.StringVar(value=cfg["base_url"])
    v_key = tk.StringVar(value="")
    v_model = tk.StringVar(value=cfg["model"])
    v_timeout = tk.StringVar(value=str(cfg["timeout"]))
    v_run_timeout = tk.StringVar(value=str(cfg["run_timeout"]))
    v_web = tk.BooleanVar(value=bool(cfg.get("web_search")))

    def cfg_row(r, label, var):
        ttk.Label(cfg_frame, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=2)
        ttk.Combobox(cfg_frame, textvariable=var).grid(row=r, column=1, sticky="ew", padx=8, pady=2)

    preset_combo = ttk.Combobox(cfg_frame, textvariable=preset_var,
                                values=list(PLATFORM_PRESETS.keys()), state="readonly")
    ttk.Label(cfg_frame, text="Platform preset:").grid(row=0, column=0, sticky="w", padx=8, pady=2)
    preset_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=2)

    def on_preset(event=None):
        p = PLATFORM_PRESETS.get(preset_var.get())
        if not p:
            return
        if event is not None:  # 仅用户手动切换预设时覆盖地址/模型；初始调用只联动模型列表
            v_base.set(p["base_url"])
            v_model.set(p["model"])
            note_var.set(p["note"])
        # 模型下拉框随平台联动：只显示该平台正确的模型名，避免选到格式不对的模型
        model_combo["values"] = MODELS_BY_PLATFORM.get(preset_var.get(), COMMON_MODELS)

    preset_combo.bind("<<ComboboxSelected>>", on_preset)

    cfg_row(1, "API base_url:", v_base)
    cfg_row(2, "API Key:", v_key)
    ttk.Label(cfg_frame, text="Model:").grid(row=3, column=0, sticky="w", padx=8, pady=2)
    model_combo = ttk.Combobox(cfg_frame, textvariable=v_model, values=COMMON_MODELS)
    model_combo.grid(row=3, column=1, sticky="ew", padx=8, pady=2)
    on_preset()
    cfg_row(4, "Call timeout (sec):", v_timeout)
    cfg_row(5, "Run timeout (sec):", v_run_timeout)
    ttk.Label(cfg_frame, textvariable=note_var, foreground="#8a5a00", wraplength=740,
              justify="left").grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6))
    ttk.Label(cfg_frame, text="API Key is valid for this run only, not persisted; paste again next time (or set env ARK_API_KEY).",
              foreground="#5a5a5a", wraplength=740, justify="left").grid(
        row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
    ttk.Checkbutton(cfg_frame, text="Web search (requires model support: Doubao / DeepSeek R1)",
                    variable=v_web).grid(row=8, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6))

    def read_cfg_from_gui():
        d = {
            "base_url": v_base.get().strip(),
            "api_key": v_key.get().strip(),
            "model": normalize_model_name(v_model.get(), v_base.get()),
            "temperature": 0.2,
            "timeout": int(v_timeout.get() or 280),
            "run_timeout": int(v_run_timeout.get() or 280),
            "web_search": bool(v_web.get()),
        }
        # 界面未填 Key 时，回填环境变量（ARK_API_KEY / DOUBAO_API_KEY）；界面始终不显示
        if not d.get("api_key"):
            d["api_key"] = cfg.get("api_key", "")
        return d

    btns = ttk.Frame(root)
    btns.grid(row=3, column=0, sticky="w", padx=14, pady=10)

    start_btn = ttk.Button(btns, text="Start (generate Program2→Program3 and run)")
    stop_btn = ttk.Button(btns, text="Stop")
    test_btn = ttk.Button(btns, text="Test LLM connection")
    rebuild_btn = ttk.Button(btns, text="Rebuild Program2 from last results (0 tokens)")

    def emit(msg):
        log_queue.put(str(msg))

    def on_test():
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("Info", f"Invalid number format in config: {e}")
            return
        v_model.set(new_cfg["model"])  # 界面显示规范化后的模型名，所见即所用
        state["running"] = True
        start_btn.config(state="disabled")
        test_btn.config(state="disabled")
        status_var.set("Testing connection...")

        def work():
            try:
                test_llm_connection(new_cfg, emit=emit)
            except Exception as e:
                emit(f"[连接测试] 失败: {e}")
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_start():
        task = task_var.get().strip()
        if not task:
            messagebox.showwarning("Info", "Please enter the task to complete")
            return
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("Info", f"Invalid number format in config: {e}")
            return
        save_config(new_cfg)
        state["running"] = True
        start_btn.config(state="disabled")
        log_list.delete(0, "end")
        status_var.set("Running (train → generate Program2 → generate Program3 → run)...")

        def work():
            try:
                run_training(task, new_cfg, emit=emit, open_ui=True)
            except Exception:
                pass
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_rebuild():
        """Rebuild Program2 from last training result (no LLM call, 0 tokens): no retraining needed after template updates."""
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("Info", f"Invalid number format in config: {e}")
            return
        state["running"] = True
        start_btn.config(state="disabled")
        test_btn.config(state="disabled")
        rebuild_btn.config(state="disabled")
        status_var.set("Rebuilding Program2 (no LLM call, 0 tokens)...")

        def work():
            try:
                rebuild_main_from_last(new_cfg, emit=emit, open_ui=True)
            except Exception as e:
                emit(f"[重建] 失败: {e}")
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_stop():
        STOP_REQUESTED[0] = True
        log_queue.put("[STOP] Stop requested; training will stop after current LLM call returns (cannot force-interrupt mid-call)")
        status_var.set("Stop requested...")

    def on_feedback():
        question = fb_var.get().strip()
        if not question:
            messagebox.showwarning("Info", "Please enter a feedback question first")
            return
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("Info", f"Invalid number format in config: {e}")
            return
        state["running"] = True
        start_btn.config(state="disabled")
        test_btn.config(state="disabled")
        fb_btn.config(state="disabled")
        status_var.set("Fixing via feedback (sending Program3 output + question to LLM)...")

        def work():
            try:
                feedback_fix(new_cfg, question, emit=emit, mock=False)
            except Exception as e:
                emit(f"[反馈修正] 失败: {e}")
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_open_dir():
        out_dir = os.path.join(BASE_DIR, "task_output")
        os.makedirs(out_dir, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(out_dir)  # noqa
            else:
                subprocess.Popen(["xdg-open", out_dir])
        except Exception as e:
            log_queue.put(f"打开目录失败: {e}")

    start_btn.config(command=on_start)
    start_btn.grid(row=0, column=0, padx=4)
    stop_btn.config(command=on_stop)
    stop_btn.grid(row=0, column=1, padx=4)
    test_btn.config(command=on_test)
    test_btn.grid(row=0, column=2, padx=4)
    rebuild_btn.config(command=on_rebuild)
    rebuild_btn.grid(row=0, column=3, padx=4)
    ttk.Button(btns, text="Open task output folder", command=on_open_dir).grid(row=0, column=4, padx=4)

    fb_frame = ttk.Frame(root)
    fb_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 4))
    ttk.Label(fb_frame, text="Feedback question (sent to LLM to fix Program3):").grid(row=0, column=0, sticky="w")
    fb_var = tk.StringVar()
    fb_entry = ttk.Combobox(fb_frame, textvariable=fb_var, font=("", 11))
    fb_entry.grid(row=0, column=1, sticky="ew", padx=8)
    fb_btn = ttk.Button(fb_frame, text="Send feedback & fix")
    fb_btn.grid(row=0, column=2, padx=4)
    fb_frame.columnconfigure(1, weight=1)
    fb_btn.config(command=on_feedback)

    log_list = tk.Listbox(root, height=14, font=("Courier", 9),
                          exportselection=False, activestyle="none")
    ysb = ttk.Scrollbar(root, orient="vertical", command=log_list.yview)
    xsb = ttk.Scrollbar(root, orient="horizontal", command=log_list.xview)
    log_list.config(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    log_list.grid(row=5, column=0, sticky="nsew", padx=(14, 0))
    ysb.grid(row=5, column=1, sticky="ns")
    xsb.grid(row=6, column=0, sticky="ew", padx=14)

    status_var = tk.StringVar(value="Ready")
    ttk.Label(root, textvariable=status_var, anchor="w").grid(
        row=7, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 6))

    root.columnconfigure(0, weight=1)
    root.rowconfigure(5, weight=1)
    cfg_frame.columnconfigure(1, weight=2)
    cfg_frame.columnconfigure(3, weight=1)

    def poll():
        try:
            while True:
                item = log_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "_done":
                    state["running"] = False
                    start_btn.config(state="normal")
                    test_btn.config(state="normal")
                    rebuild_btn.config(state="normal")
                    fb_btn.config(state="normal")
                    status_var.set("Done")
                    continue
                for line in str(item).splitlines() or [""]:
                    log_list.insert("end", line)
                log_list.see("end")
        except queue.Empty:
            pass
        root.after(100, poll)

    root.after(100, poll)
    return root


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(
        description="训练程序（程序1）: 命令1/2/3 → 结果1/2/3 → 自动生成 程序2(主程序) → 程序3(运行程序)")
    ap.add_argument("--selftest", action="store_true",
                    help="Headless self-test: simulate LLM through Program1→Program2→Program3")
    ap.add_argument("--run-task", metavar="Task description", default=None,
                    help="CLI real run: train + generate main program + run Program3 (API Key required)")
    ap.add_argument("--model", default=None, help="Override LLM model name")
    args = ap.parse_args()

    def _emit(msg):
        print(msg, flush=True)

    if args.selftest:
        cfg = load_config()
        print("Starting self-test (mock LLM, no real API)...")
        run_training("Self-test task: capture 5 gold-related indicators from 10 webpages (5 states), output one of the decisions (Buy_Full/Sell_Full/Buy_Half/Sell_Half/No_Trade)", cfg, emit=_emit, mock=True)
        return

    if args.run_task:
        cfg = load_config()
        if args.model:
            cfg["model"] = args.model
        if not cfg.get("api_key"):
            print("Error: LLM API Key not configured (env ARK_API_KEY or config file)", flush=True)
            import sys as _sys
            _sys.exit(2)
        print("Starting CLI task run (real LLM): %s" % args.run_task, flush=True)
        run_training(args.run_task, cfg, emit=_emit)
        return

    root = build_gui()
    root.mainloop()


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
