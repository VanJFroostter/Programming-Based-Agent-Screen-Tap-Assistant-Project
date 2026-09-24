#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练程序.py —— 程序1（训练程序，带可视化界面）

生成链: 程序1(本程序) → 自动生成 程序2(主程序.py) → 自动生成 程序3(运行程序.py)

界面显示「您要完成什么任务？」，用户输入任务文字后，一键完成:
  1. 命令1 → LLM → 结果1（以 json 定义 state/choice 及严格识别标准）
  2. 命令2 → LLM → 结果2（state→choice 的运算流程程序）
  3. 命令3 → LLM → 结果3（脚本/Agent 分类程序）
  4. 新建文件夹，并在其中建立 子文件夹 和 记忆（保存 结果1/2/3）
  5. 自动生成 程序2 = 主程序.py（内嵌 结果1/2/3 与 LLM 配置，可独立运行）
  6. 自动运行 程序2:
       - 发送「请你根据以下标准识别：\n并且按照json格式严格输出」+ 结果1 → LLM → 结果4
       - 将 结果4+结果2+结果3 组装生成 程序3 = 子文件夹/运行程序.py
       - 运行 程序3，运行输出保存到 记忆/运行输出.txt

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
    '现在要完成“{user_task}”任务。注意：本次对话无需完成任务本身，只需规划任务流程。'
    '请先做一个程序设计规划（仅规划大致思路不写代码）。'
    '先理解两个核心概念：'
    '【state = 输入情况参数】：任务当前所处的各种“情况/条件”，每个 state 都可以观察或判定出'
    '当前的具体取值（离散枚举，如 晴/雨、上涨/下跌、紧张/缓和）。它描述“现在是什么情况”。'
    '【choice = 输出动作/决策】：程序根据 state 判断后要做的“操作/决策”，是可执行的最终动作'
    '（如 买入/卖出/不操作、下单/取消、通过/拒绝）。'
    '流程就是：state（情况）→ 最终 choice（执行动作）。不要定义任何中间决策节点'
    '（不要 choice1、choice2、… 这类推理中间步骤），state 直接决定最终 choice。'
    '【概念示例（仅为帮你理解 state/choice 的含义，严禁照搬取值到本任务）】'
    '  任务“判断出门要不要带伞”：state = 今天天气(晴/雨/阴)、出门时段(早/中/晚)；'
    '  最终 choice = 带伞/不带伞。state 直接决定最终 choice，没有中间步骤。'
    '  例子里“天气、时段”是情况参数（state），“带伞/不带伞”是动作（choice）。'
    '  注意这只是例子：你的任务必须用自己的分析得出自己的 state 和 choice，不得套用此例。'
    '请针对当前任务自主分析并输出规划：'
    '① 需要哪些输入状态 state（每个的取值）；② 输出动作 choice（最终 choice 的取值，必须是可直接执行的动作）；'
    '③ state→最终 choice 的运算流程（从输入到最终动作的步骤链）；④ 最终 choice 将如何执行（文字反馈或脚本）。'
    '用结构化文字清晰输出规划；只做规划思路，不要写任何程序代码。'
)

CMD1_TEMPLATE = (
    '我现在要完成“{user_task}”任务，请把该工作分割成输入状态state和输出动作choice来完成。'
    '【概念】state = 需要观察/判定的“情况参数”，每个都可判定当前取值并给出离散枚举'
    '（如 晴/雨、上涨/下跌、紧张/缓和）；choice = 根据情况做出的“动作/决策”，'
    '是可执行的最终动作（如 通过/拒绝、下单/取消）。'
    '请自主从本任务中分析：哪些“情况”需要观察（→state），哪些“操作”需要决策（→choice），'
    '并给出各自的离散取值。取值必须来自你对本任务的分析，禁止照搬任何示例、禁止套用模板。'
    '请以严格的JSON语言定义：至少1个输入状态state，至少1个输出动作choice'
    '（choices 数组只定义最终输出动作 choice，不定义任何中间推理步骤；'
    '如需多个动作可并列定义，但每个都必须可直接执行）。每个state或choice可以有2个及以上的取值。'
    '请只输出一个JSON对象，内容仅为所有state和choice的定义，'
    '不要输出JSON以外的任何文字、解释或代码块围栏。JSON格式：'
    '{{"need_loop": true或false, '
    '"states": [{{"name": "<state名>", "values": ["<取值1>", "<取值2>", ...], '
    '"识别标准": "<如何识别该state当前取值的严格标准>"}}], '
    '"choices": [{{"name": "<choice名>", "values": ["<取值1>", "<取值2>", ...], '
    '"role": "最终动作", '
    '"识别标准": "<如何识别该choice判定的严格标准>"}}]}}'
    '其中 need_loop 表示该任务是否需要多次循环执行：一次性任务请设为 false（避免程序循环等待）；'
    '仅持续监控/实时数据更新类任务才设为 true。'
)

BUILTIN_DATA_URLS = (
    '【联网搜索模式】所有实时/外部数据由 LLM 通过已开启的联网搜索（web_search）直接获取，'
    '无需声明 URL、无需本地程序抓取。\n'
)


CMD_COLLECT_PLAN = (
    '请根据结果1 中定义的每个 state（输入状态），设计信息收集方案。'
    '【本任务使用 LLM 信息采集路径】：所有 state 的当前取值一律由 LLM 获取并转换为 JSON，'
    '不编写、不运行任何本地采集脚本。'
    '对每个 state，请说明 LLM 应如何获取它的当前真实取值：'
    '（例如查询哪些公开网页/接口、读取哪个本地文件、依据什么规则推断，或需要用户提供什么信息），'
    '并明确标注：哪些 state 的取值依赖用户运行时手动输入'
    '（在程序2 的「信息输入」框里填写，一行一项；如账号、密码、授权码等登录凭据）。'
    '【实时/外部数据】需要天气、行情、网页信息等实时数据的 state：'
    'LLM 已开启联网搜索能力（web_search），直接说明"用联网搜索获取该实时数据"即可；'
    '禁止声明 URL、禁止要求本地程序抓取网页。'
    + BUILTIN_DATA_URLS +
    '无法通过联网搜索/常识获取的取值，一律标注为「用户输入」。'
    '输出要精简，总字数控制在 400 字以下，用简洁的文字描述即可，不用 JSON。'
)

CMD2 = (
    '生成一个运算程序（Python 代码）。该程序运行时，state 当前取值 JSON 已作为常量 RESULT4 '
    '提供（也可从环境变量 MAIN_RESULT4 读取，JSON 字符串，只包含每个 state 的当前取值，'
    '不包含 choice）。请编写 Python 代码：根据 RESULT4 中的 state 取值，直接计算最终输出动作'
    '（不要定义 choice1/choice2 等中间节点，state 直接决定最终动作），'
    '最后输出严格的 JSON 决策（{"decision": {"message": "文字结果"} 或 '
    '{"decision": {"script": "真正的 Python 代码"}}）。'
    '【重要】message 字段填最终文字结论；script 字段只填真正可执行的 Python 代码，'
    '不要把动作文字（如“买入”）填进 script。'
    '【重要】严格只输出 Python 程序代码，不要任何解释文字、说明或 markdown 标记。'
)

CMD3_EXEC = (
    '根据前面的运算流程（结果2），编写完整的决策执行程序，合并成一个完整的 Python 程序：\n'
    '1. 根据最终 choice 的执行方式编写执行逻辑：\n'
    '   - 文字反馈：直接 print 输出 JSON 决策 {"decision": {"message": "文字内容"}}（message 只填文字结论）\n'
    '   - 脚本：直接执行脚本逻辑并 print 执行结果，输出 JSON 决策 {"decision": {"script": "脚本代码"}}（script 只填真正可执行的 Python 代码，禁止把动作文字填进 script）\n'
    '2. 【全自动运行硬性要求，必须遵守】\n'
    '   - 程序启动后自动、一次性运行完整流程并结束，禁止等待任何用户点击\n'
    '   - 禁止使用 input()、messagebox、tkinter 窗口、mainloop、时间 sleep、循环等待用户操作\n'
    '   - 所有输入数据已包含在常量 RESULT4 中（JSON 字符串，含每个 state 的当前取值），'
    '直接解析 RESULT4 使用，不要重新采集、不要让用户提供任何东西\n'
    '   - 一切输出用 print 打印（会自动显示在程序3 运行窗口）；最终 JSON 决策必须单独一行完整输出，方便解析\n'
    '   - 【必须做出唯一决策】程序3 运行结束时必须 print 一行 JSON：{"decision": {"message": "最终决策文字"}}，'
    '从任务定义的动作中唯一取值；【禁止】只打印各个决策选项的说明文字而不输出最终决策；'
    '即使数据不足/网页获取失败，也要给出决策（数据不足时默认输出“不买入不卖出”等观望类取值，并说明原因）\n'
    '   - 【参数输入框：给程序自己用，不要发给 LLM】程序3 运行窗口内置参数输入框，'
    'AI 代码必须在顶层声明输入框标签：\n'
    '     REQUIRED_INPUTS = ["标签1", "标签2", ...]   # 最多5个，纯字符串列表；不需要参数就写 []\n'
    '     窗口会自动按这些标签生成输入框，用户填写后点「提交参数」。\n'
    '     程序需要这些参数时调用 window_inputs() 获取 {"标签": "用户填的值"} 字典，'
    '按参数执行不同逻辑（如查询数量、筛选关键词、处理条数等）。\n'
    '     登录类任务（需要账号/密码/授权码等凭据的服务）把凭据也作为 REQUIRED_INPUTS 标签，'
    '用户填写后直接用，例如：\n'
    '     REQUIRED_INPUTS = ["账号", "密码"]\n'
    '     ...\n'
    '     p = window_inputs()\n'
    '     client = my_client(p["账号"], p["密码"])  # 用标准库自行实现\n'
    '     参数给程序自己用：禁止把凭据发回 LLM、禁止自己实现输入界面、禁止 input()；'
    '登录失败时把错误打印出来，不要静默继续\n'
    '   - 【其他需要用户选择/确认的输入】用内置函数 window_in("提示语")（在程序3 窗口弹输入框）\n'
    '   - 优先从常量 ENV_INFO 读取已提供的环境信息，不要重复询问\n'
    '   - 程序3 窗口输出时不要用 "choice1结果/choice2结果" 这类标签，直接用中文步骤名'
    '（如 "第1步 采集"、"第2步 识别"、"最终决策"）\n'
    '3. 【最终结果必须可见】任务做完后，把最终结果写入文件 记忆/最终结果.txt'
    '（先创建 记忆 目录；【文件名必须是 最终结果.txt，固定不变，禁止自定义成其他文件名'
    '】，然后用系统默认程序打开该文件'
    '（Windows: os.startfile，macOS: open，Linux: xdg-open），'
    '让用户直接看到最终结果；不要弹任何阻塞式窗口；'
    '【重要】用 open/xdg-open 打开文件时，必须写 subprocess.Popen(["xdg-open", 路径], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)，'
    '禁止让打开文件的子进程继承标准输出管道（否则父进程会一直等它结束）。\n'
    '4. 【环境信息】用户提供的登录/个人资料等信息已注入常量 ENV_INFO（JSON 对象，'
    '（用户提供的信息，可能为空字符串）。需要凭据、账号、服务器地址等时，'
    '从 ENV_INFO 读取，不要自己假设或让用户再次输入\n'
    '【重要】严格只输出完整的 Python 程序代码（含 import、函数定义、执行逻辑），不要任何解释文字、说明或 markdown 标记。'
)

P3_FRAMEWORK_HINT = (
    '【程序3 运行框架（精简版源码，你的代码将注入其中运行，请严格按此框架编写）】\n'
    '程序3 = 内置运行窗口壳 + 你的代码。窗口壳已实现窗口显示与输入框，以下精简框架源码已就绪，你的代码运行在其中：\n'
    '```python\n'
    '# 已就绪常量（直接使用，不要重新采集）：\n'
    'RESULT4 = \'{"state1": "取值", ...}\'    # state 取值 JSON 字符串；亦可读 os.environ[\'MAIN_RESULT4\']\n'
    'ENV_INFO = \'{...}\'                      # 用户环境信息 JSON 字符串（凭据等，可能为空字符串）\n'
    'COLLECT = RESULT4                         # 同 RESULT4\n'
    '# 已就绪内置函数（窗口壳已实现，直接调用）：\n'
    'window_out(文本)                          # 显示运行状况\n'
    'window_in("提示语")                       # 需要用户输入时调用，弹输入框返回字符串\n'
    'window_inputs()                           # 按 REQUIRED_INPUTS 标签渲染输入框，返回 {"标签": "值"} 字典\n'
    'show_text_result(文本) / run_script_with_window(代码)   # 文字/脚本反馈\n'
    '# batch_util 已内置：load_csv_batch / batch_llm_query / batch_loop_run / save_batch_result\n'
    '```\n'
    '窗口壳行为：窗口一直显示运行输出；你的代码不需要用户输入时自动运行到结束（窗口自动关闭）；\n'
    '调用 window_in/window_inputs 时窗口显示文字提示，等用户输入后才继续。\n'
    '顶层声明：REQUIRED_INPUTS = ["标签1", ...]（最多5个；需要登录凭据/参数时声明，不需要写 []，窗口将只显示运行状况）。\n'
    '示例骨架（你的代码应类似）：\n'
    '```python\n'
    'import json\n'
    'REQUIRED_INPUTS = []\n'
    'r4 = json.loads(RESULT4) if isinstance(RESULT4, str) else RESULT4\n'
    '# ... 根据 state 执行 choice 逻辑 ...\n'
    'print(json.dumps({"decision": {"message": "结果"}}, ensure_ascii=False))  # 最终决策单独一行完整输出\n'
    '```\n'
)



CMD5 = (
    '现在请为这个任务设计一套核验标准（不要实际核验，只设计标准）。'
    '核验标准包括两部分：\n'
    '1. 流程核验：state→最终输出 的计算流程是否完整、覆盖所有分支\n'
    '2. 输出结果核验：最终输出的决策（choice）是否合理、是否符合任务目标\n'
    '输出要精简，总字数控制在 400 字以下，用简洁的文字描述即可，不用 JSON。'
)

IDENTIFY_PREFIX = '请你根据以下标准识别：\n并且按照json格式严格输出\n'

SYSTEM_PROMPT = '你是严谨的程序设计助手。回答准确、结构清晰；需要代码时用 python 代码块给出。'

# ---------------------------------------------------------------- 可用工具注册表（写入训练 Prompt，告知 LLM 可调用哪些工具）

# 完整工具清单：注入命令0（整体规划）——已精简压缩，避免长请求触发服务端挂起
TOOLS_REGISTRY = (
    '【可用工具清单（已内置、无需安装，写代码时可直接调用；禁止使用清单外的任何第三方库）】\n'
    '一、batch_util（内置）：\n'
    '  load_csv_batch(文件路径) → 读CSV为list[dict]；\n'
    '  batch_llm_query(文本列表, 系统指令="", 批量=30) → 打包一次LLM调用返回JSON数组（顺序一致；仅主程序环境可用）；\n'
    '  save_batch_result(保存路径, 结果列表, 摘要=None, 失败=None) → 写CSV并返回汇总JSON文本；\n'
    '  batch_loop_run(函数, 数据列表) → 批量循环逐条执行，自动统计失败。\n'
    '二、程序3 内置窗口接口（程序3 运行时自动提供，写执行代码时可直接调用）：\n'
    '  window_out(文本) → 把文本显示到程序3 运行窗口；\n'
    '  window_in("提示语") → 弹输入框等用户输入一般信息（用户输入后返回字符串）；\n'
    '  REQUIRED_INPUTS → AI 代码顶层声明的参数输入框标签列表，如 REQUIRED_INPUTS = ["查询数量？", "筛选关键词"]'
    '（最多5个，纯字符串列表；不需要参数写 []）。程序3 窗口会自动按这些标签生成输入框；\n'
    '  window_inputs() → 返回用户在窗口参数输入框填写的字典 {"标签": "用户填的值"}'
    '（给程序自己用的参数，如登录凭据/数量/筛选条件；禁止把凭据发给 LLM）。\n'
    '三、预留扩展（未部署时禁止写代码）：云端Agent（agent_sync_call等）、截图视觉（capture_full_screen等）。\n'
    '硬性约束：只允许 import 内置 batch_util 与 Python 标准库；'
    '禁止第三方库（requests/pandas/pyautogui/selenium等）。'
)

# 批量任务约束：注入命令3（运算程序）与命令4（执行程序）
BATCH_GUIDANCE = (
    '【批量任务约束（若任务涉及大批量输入处理，如多行记录/批量文件，必须遵守）】\n'
    '1. 程序必须用内置 batch_util 工具封装批量处理：load_csv_batch 读入批量输入、batch_loop_run 循环逐条运算、'
    'save_batch_result 输出汇总；\n'
    '2. 禁止逐条串行调用 LLM；需要语义理解时用 batch_llm_query 一次打包多条返回 JSON 数组；\n'
    '3. 最终输出必须是单个 JSON 对象：{"summary": {"总数":..., "分类统计":...}, '
    '"results": [{"id":..., "决策":...}, ...], "failed": [{"item":..., "error":...}]}；'

    '4. 仅允许使用内置 batch_util 与 Python 标准库，禁止 import 清单外第三方库。'
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
            "batch_llm_query 需要 LLM 能力，但当前运行环境未注入 BATCH_LLM_FN"
            "（仅主程序可用，程序3 中不可用）")
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
            "请对以下输入逐条处理，只输出一个 JSON 数组（长度与输入一致、顺序一致，每项保留 index 字段）：\\n" + _items})
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
    """批量结果写 CSV，返回 {"summary":..., "results":[...], "failed":[...]} JSON 文本。"""
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
    """批量循环封装：逐条调用 run_func(item)，自动统计失败；返回 (成功列表, 失败列表)。"""
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
                f"{label}: 使用了工具清单外的库 {_m.group(1)}"
                f"（仅允许内置 batch_util 与标准库，禁止第三方库）")
    try:
        compile(code, "<check>", "exec")
    except SyntaxError as _e:
        problems.append(f"{label}: 语法错误 {_e}")
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
        print(f"警告: 读取 config.json 失败（{e}），使用默认配置", file=sys.stderr)
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
    "火山方舟/豆包": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-lite-260428",
        "note": "密钥=控制台 APIKey（UUID 风格）；model=控制台模型 ID（如 doubao-seed-2-0-lite-260428）或接入点 ID（ep- 开头），从「开通管理」复制",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "密钥以 sk- 开头",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "密钥以 sk- 开头",
    },
    "通义千问(阿里云百炼)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "密钥以 sk- 开头",
    },
    "智谱GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "note": "密钥以 数字.数字 开头（如 123.abc...）",
    },
    "Kimi(Moonshot)": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "note": "密钥以 sk- 开头",
    },
}

# 各平台常用模型名（下拉框随平台预设联动；均可手动输入任意模型 ID）
MODELS_BY_PLATFORM = {
    "火山方舟/豆包": [
        "doubao-seed-2-0-lite-260428",
        "doubao-seed-2-0-mini-260428",
    ],
    "OpenAI": ["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini", "o3-mini"],
    "DeepSeek": ["deepseek-chat", "deepseek-reasoner"],
    "通义千问(阿里云百炼)": ["qwen-plus", "qwen-max", "qwen-turbo"],
    "智谱GLM": ["glm-4-flash", "glm-4-plus", "glm-4-air"],
    "Kimi(Moonshot)": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
}

# 兼容旧版：仅用于读取旧配置时的模型名升级
COMMON_MODELS = [m for lst in MODELS_BY_PLATFORM.values() for m in lst]


def _http_hint(code, base, model):
    """把 HTTP 状态码翻译成中文排查建议。"""
    if code in (401, 403):
        return ("认证失败：① API Key 是否复制完整、首尾无空格？② 该 Key 是否与所选平台匹配"
                "（火山方舟的 Key 不能填在 OpenAI/DeepSeek 地址上）？③ 火山方舟请用控制台创建的 "
                "APIKey（UUID 格式），并在控制台开通对应模型/推理接入点。")
    if code == 404:
        return (f"地址或模型名不正确：确认接口地址路径完整，且模型名 {model!r} 在当前账号可用。"
                "火山方舟提示：模型名请从控制台「开通管理」复制小写模型 ID（如 doubao-seed-2-0-lite-260428）"
                "或推理接入点 ID（ep- 开头）；不要直接复制页面显示名（如 Doubao-Seed-2.0-lite 260428），"
                "也不要选未开通的模型。")
    if code == 429:
        return "请求过于频繁或额度不足：稍后重试，或检查账号余额/限流设置。"
    if code >= 500:
        return "平台服务端暂时故障：稍后重试。"
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
    """快速诊断 DNS 解析与 TCP 连通性，区分网络问题与接口问题。"""
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
        return (f"DNS 解析 {host} → {addrs}（{dns_t:.2f}s）；"
                f"TCP 连接 {host}:{port} 成功（{tcp_t:.2f}s）。网络层可达，"
                f"超时发生在等待 LLM 服务端返回数据阶段。")
    except Exception as e:
        return f"网络诊断：{e}（若此处报错，说明 DNS 或 TCP 连接本身就不通）"


# ---------------------------------------------------------------- LLM 对话记录
# 所有 LLM 调用（发送+接收）统一记录，训练结束/程序运行后写入「记忆/LLM对话记录.txt」
LLM_DIALOG_LOG = []        # 模块级：[{send, receive, ok, mock}]
CURRENT_MEM_DIR = [None]   # 当前任务记忆目录：训练程序运行时设置，自动写盘对话记录


def flush_dialog_log(mem_dir, emit=print):
    """把 LLM_DIALOG_LOG 全量写入 记忆/LLM对话记录.txt。"""
    if not mem_dir:
        return
    try:
        os.makedirs(mem_dir, exist_ok=True)
        parts = []
        for i, d in enumerate(LLM_DIALOG_LOG, 1):
            parts.append(
                "【对话 %d%s】\n【发送】\n%s\n【接收】\n%s" % (
                    i, "（模拟）" if d.get("mock") else "",
                    json.dumps(d.get("send", []), ensure_ascii=False, indent=1),
                    d.get("receive", "")))
        save_text(os.path.join(mem_dir, "LLM对话记录.txt"), "\n\n".join(parts))
    except Exception as e:
        emit(f"[记录] 写入 LLM 对话记录失败: {e}")


def call_llm(cfg, messages):
    """调用 OpenAI 兼容的 chat/completions 接口，返回回复文本。"""
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    if not base.startswith("http://") and not base.startswith("https://"):
        raise RuntimeError(f"接口地址格式错误: {base!r}（应以 http:// 或 https:// 开头）")
    if not key:
        raise RuntimeError("API Key 为空：请在界面配置区粘贴 API Key，"
                           "或设置 ARK_API_KEY / DOUBAO_API_KEY 环境变量")
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
        payload = {
            "model": model,
            "messages": messages,
            "temperature": cfg.get("temperature", 0.2),
            "stream": False,
        }
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
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[HTTP 错误 {e.code}] {detail}", "ok": False})
        hint = _http_hint(e.code, base, model)
        raise RuntimeError(
            f"LLM 接口错误 {e.code}\n"
            f"请求地址: {url}\n"
            f"平台响应: {detail}\n"
            + (("排查建议: " + hint) if hint else ""))
    except Exception as e:
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[异常] {e}", "ok": False})
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            diag = _diag_network(url)
            prox = [p for p in ("http_proxy", "https_proxy", "all_proxy")
                    if os.environ.get(p) or os.environ.get(p.upper())]
            prox_txt = ("检测到系统代理环境变量: " + ", ".join(prox) +
                        " → 若 curl 也超时，请先临时关闭代理/VPN 再试。" if prox
                        else "未检测到系统代理环境变量。")
            curl_body = json.dumps({"model": model, "messages": messages,
                                    "temperature": cfg.get("temperature", 0.2)},
                                   ensure_ascii=False)[:300]
            raise RuntimeError(
                f"LLM 请求超时（已等待 {cfg.get('timeout', 280)} 秒）：可能是 Prompt 过长或网络较慢。\n"
                f"请求地址: {url}\n原始错误: {e}\n{diag}\n{prox_txt}\n"
                f"自测命令（在终端运行，替换 <你的APIKey> 后可直接判断是否接口问题）:\n"
                f"  curl -sS -m 60 {url} \\\n"
                f"    -H 'Authorization: Bearer <你的APIKey>' -H 'Content-Type: application/json' \\\n"
                f"    -d '{curl_body}'\n"
                f"判断：① curl 也超时 → 网络/代理问题；② curl 快速报 401/404 → Key 或模型名问题"
                f"（见「测试连接」按钮的排查建议）；③ curl 正常返回 → 请把「调用超时」调大到 600 秒再试。")
        raise RuntimeError(f"无法连接 LLM 接口（{base}），请检查网络或地址是否正确: {e}")
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
        raise RuntimeError(f"LLM 返回格式异常: {str(body)[:300]}")
    LLM_DIALOG_LOG.append({"send": messages, "receive": content, "ok": True})
    return content


# ---------------------------------------------------------------- 自检（模拟 LLM）

MOCK_RESULTS = {
    0: ('规划：① 捕捉10个黄金相关网页，提取5个情况参数（state1 黄金价格、state2 24h涨跌幅、'
        'state3 美元指数、state4 避险情绪、state5 ETF资金净流入）；'
        '② 最终决策动作（买入全仓/卖出全仓/买入半仓/卖出半仓/不买入不卖出）；'
        '③ 根据5个情况参数直接综合判断输出最终决策；④ 文字反馈。'),
    1: ('{"states": [{"name": "state1", "values": ["上涨", "下跌", "震荡"], '
        '"识别标准": "根据国际现货黄金价格变动趋势识别state1取值"}, '
        '{"name": "state2", "values": ["强势", "弱势", "中性"], '
        '"识别标准": "根据金价24小时涨跌幅强弱识别state2取值"}, '
        '{"name": "state3", "values": ["走强", "走弱", "震荡"], '
        '"识别标准": "根据美元指数走势识别state3取值"}, '
        '{"name": "state4", "values": ["高", "中", "低"], '
        '"识别标准": "根据全球避险情绪/风险事件程度识别state4取值"}, '
        '{"name": "state5", "values": ["净流入", "净流出", "持平"], '
        '"识别标准": "根据黄金ETF资金净流入方向识别state5取值"}], '
        '"choices": [{"name": "黄金交易动作", '
        '"values": ["买入全仓", "卖出全仓", "买入半仓", "卖出半仓", "不买入不卖出"], '
        '"role": "最终动作", "识别标准": "根据5个指标综合判断买入/卖出决策"}]}'),
    2: ('```python\n'
        'import json\n'
        'r4 = json.loads(RESULT4) if isinstance(RESULT4, str) else RESULT4\n'
        'print("结果2(运算流程) 读取state:", r4.get("state1"), r4.get("state2"), r4.get("state3"), r4.get("state4"), r4.get("state5"))\n'
        's1 = r4.get("state1"); s4 = r4.get("state4"); s5 = r4.get("state5")\n'
        'if s4 == "高" and s1 == "上涨":\n'
        '    decision = "买入全仓"\n'
        'elif s1 == "下跌" and s5 == "净流出":\n'
        '    decision = "卖出全仓"\n'
        'elif s4 == "中" and s1 == "上涨":\n'
        '    decision = "买入半仓"\n'
        'elif s1 == "下跌":\n'
        '    decision = "卖出半仓"\n'
        'else:\n'
        '    decision = "不买入不卖出"\n'
        'print(json.dumps({"decision": {"message": decision}}, ensure_ascii=False))'),
    3: ('```python\n'
        'import json\n'
        'print("结果3(执行程序) 根据决策执行反馈")\n'
        'show_text_result("最终决策已执行")'),
    4: ('```python\n'
        'def show_text_result(msg):\n'
        '    print("【窗口·文字】", msg)\n'
        'def run_script_with_window(code, title="正在执行脚本"):\n'
        '    exec(compile(code, "<script>", "exec"))'),
    5: '{"indicators": ["每个state/choice定义与识别标准一致"], '
       '"self_checks": [{"indicator": "定义与识别标准一致", "passed": true, "note": "通过"}], '
       '"conclusion": "校验通过（模拟）"}',
    6: 'OK: 无需修改',
    "collect_plan": ('{"states_collect": ['
                     '{"state": "state1", "method": "prompt", "source": "国际现货黄金价格（美元/盎司）", "script": null}, '
                     '{"state": "state2", "method": "prompt", "source": "金价24小时涨跌幅", "script": null}, '
                     '{"state": "state3", "method": "prompt", "source": "美元指数走势", "script": null}, '
                     '{"state": "state4", "method": "prompt", "source": "全球避险情绪/风险事件", "script": null}, '
                     '{"state": "state5", "method": "prompt", "source": "黄金ETF资金净流入", "script": null}], '
                     '"note": "5个指标均需通过联网搜索（web_search）获取黄金相关实时数据，捕捉10个网页信息"}'),
    "feedback": ('【修改结果3】\n'
                 '```python\n'
                 'import json\n'
                 'print("结果3(反馈修正版) 根据决策执行反馈")\n'
                 'show_text_result("最终决策已执行")\n'
                 '```'),
    "new_chat": 'OK: 已开启新对话（模拟）。',
    "fix_code": ('```python\n'
                 'import json\n'
                 'r4 = json.loads(RESULT4) if isinstance(RESULT4, str) else RESULT4\n'
                 'print("结果2(修复版) 读取state:", r4.get("state1"), r4.get("state2"), r4.get("state3"), r4.get("state4"), r4.get("state5"))\n'
                 'decision = "买入全仓" if r4.get("state4") == "高" and r4.get("state1") == "上涨" else "不买入不卖出"\n'
                 'print(json.dumps({"decision": {"message": decision}}, ensure_ascii=False))\n'
                 '```\n'
                 '```python\n'
                 'import json\n'
                 'print("结果3(修复版) 执行反馈")\n'
                 'show_text_result("最终决策已执行")\n'
                 '```'),
    "fix_runtime": ('```python\n'
                    'import json\n'
                    'print("结果3(运行修复版) 执行反馈")\n'
                    'show_text_result("最终决策已执行")\n'
                    '```'),
}

# 模拟：转换步骤输出的 state 当前取值 JSON（只含 state，不含 choice）
MOCK_RESULT4 = '{"state1": "上涨", "state2": "强势", "state3": "走弱", "state4": "高", "state5": "净流入"}'

MOCK_CONNECTION_REPLY = '__CONNECTION_OK__ LLM连接成功（模拟）。'


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
    """向 LLM 真实发送一条带校验标记的测试消息，必须收到含标记的真实回复才算连接成功。"""
    def _mask_key(k):
        k = (k or "").strip()
        return (k[:4] + "****" + k[-4:]) if len(k) > 8 else ("*" * len(k))
    # 测试连接用短超时（最多60秒）：快速失败，避免服务器hang时用户干等180秒"没有反应"
    test_cfg = dict(cfg)
    test_cfg["timeout"] = 60
    emit(f"[连接测试] 真实发送测试消息 → {cfg.get('base_url')}/chat/completions | "
         f"模型: {normalize_model_name(cfg.get('model'), cfg.get('base_url'))} | Key: {_mask_key(cfg.get('api_key'))} ...")
    emit("[连接测试] 正在等待 LLM 响应（最多 60 秒），请稍候 ...")
    if mock:
        reply = MOCK_CONNECTION_REPLY
        emit("[连接测试] （模拟模式）返回模拟回复")
    else:
        try:
            reply = call_llm(test_cfg, [{"role": "user",
                                        "content": "1"}])
        except Exception as e:
            emit(f"[连接测试] 失败：{e}")
            emit("[连接测试] 排查建议：")
            emit("  1. 如果是超时：pro 模型是推理模型，响应较慢，可以把「调用超时」调大到 300 秒再试")
            emit("  2. 如果是 401/认证失败：检查 API Key 是否复制完整、首尾无空格")
            emit("  3. 如果是 404/模型不存在：到火山方舟控制台「开通管理」确认该模型已开通，或复制推理接入点 ID（ep- 开头）")
            raise
    emit("[连接测试] LLM 原始回复全文:")
    for line in (reply or "").splitlines()[:20]:
        emit("  | " + line)
    if reply and reply.strip():
        emit("[连接测试] 成功：LLM 已返回内容，连接正常。")
    else:
        emit("[连接测试] 警告：LLM 返回为空。")
    rec = os.path.join(BASE_DIR, "LLM连接测试记录.txt")
    save_text(rec, reply)
    emit(f"[连接测试] 回复已储存: {rec}")
    return reply


# ---------------------------------------------------------------- 工具函数

def summary(text, n=200):
    t = (text or "").replace("\n", " ").strip()
    return t[:n] + ("..." if len(t) > n else "")


def decode_bytes(b):
    """子进程输出按 utf-8 → gbk → latin-1 依次尝试解码，避免 Windows 下乱码。"""
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
    """兼容嵌套包裹格式：{"need_loop":..., "states": {"名": "值"}} → 扁平 {"名": "值"}。"""
    if not isinstance(parsed, dict):
        return parsed
    _st = parsed.get("states")
    if isinstance(_st, dict):
        parsed = dict(_st)
    elif isinstance(_st, list):
        _flat = {}
        for _item in _st:
            if isinstance(_item, dict) and _item.get("name"):
                _flat[_item["name"]] = (_item.get("value") or _item.get("取值")
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
    """程序1 侧重新运行 程序3（反馈修正后重跑用）：捕获输出并保存到 记忆/运行输出.txt。"""
    sub_dir = os.path.join(root_dir, "子文件夹")
    mem_dir = os.path.join(root_dir, "记忆")
    py3 = os.path.join(sub_dir, "运行程序.py")
    if not os.path.exists(py3):
        emit("[反馈修正] 未找到程序3，请先完成训练")
        return -1
    env = dict(os.environ)
    env["MAIN_RESULT4"] = json.dumps(flatten_state_json(extract_json(result4) or {}), ensure_ascii=False)
    env["P3_NO_GUI"] = "1"   # 静默模式：不弹窗口，输出走控制台
    emit("[反馈修正] 重新运行 程序3 ...")
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
                emit("[反馈修正] 程序3 运行超时，已终止")
            out_b, err_b = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_b, err_b = proc.communicate()
        out = (decode_bytes(out_b) or "") + \
              ("\n" + decode_bytes(err_b) if err_b else "")
        save_text(os.path.join(mem_dir, "运行输出.txt"), out)
        emit("[反馈修正] 程序3 退出码: " + str(proc.returncode))
        emit(out[-3000:] if out.strip() else "（无输出）")
        return proc.returncode
    except Exception as e:
        emit(f"[反馈修正] 运行程序3 失败: {e}")
        return -1


def _parse_feedback_parts(reply):
    """从 LLM 反馈回复中解析「段位标签」修改指令（容错：标签可带冒号/解释文字/顺序任意）。
    判定依据：每个代码块之前的文本（前 200 字符）内是否出现 结果2/结果3（或 result2/result3），
    支持【修改结果2】、【修改结果3：】、结果3:、无括号等变体，也兼容 JSON 指令
    {"result2": "...", "result3": "..."}。返回 {"result2": 代码, "result3": 代码}（只含被修改的段）。"""
    parts = {}
    for _m in re.finditer(r"```[a-zA-Z]*\s*\n(.*?)```", reply, re.S):
        _code = _m.group(1).strip()
        if not _code:
            continue
        _head = reply[max(0, _m.start() - 200):_m.start()]
        _tags = list(re.finditer(r"结果\s*([23])|result\s*([23])", _head, re.I))
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
    cur = os.path.join(BASE_DIR, "任务输出", "当前任务.txt")
    if not os.path.exists(cur):
        emit("[反馈修正] 尚未训练过任何任务，请先执行训练")
        return
    with open(cur, "r", encoding="utf-8-sig") as f:
        root_dir = f.read().strip()
    sub_dir = os.path.join(root_dir, "子文件夹")
    mem_dir = os.path.join(root_dir, "记忆")
    py3 = os.path.join(sub_dir, "运行程序.py")
    if not os.path.exists(py3):
        emit("[反馈修正] 未找到程序3（子文件夹/运行程序.py），请先完成训练并运行")
        return

    def read(name):
        p = os.path.join(mem_dir, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                return f.read()
        return ""

    task = read("任务描述.txt") or os.path.basename(root_dir)
    r1 = read("结果1.txt")
    r6 = read("结果6.txt")
    r4 = read("识别结果.txt")
    out = read("运行输出.txt")
    cur_r2 = read("结果2.txt")
    cur_r3 = read("结果3.txt")
    with open(py3, "r", encoding="utf-8-sig") as f:
        code = f.read()

    prompt = (
        "你是程序3（自动生成的运行程序）的 AI 逻辑修理工。程序3 由【系统框架（不可修改）】"
        "（内置 batch_util、信息常量、本地采集、窗口壳）和【AI 逻辑（可修改）】两部分组成。\n"
        "AI 逻辑只有两段：结果2（state→choice 运算流程）、结果3（choice 执行程序）。"
        "你只能修改这两段，并且必须声明新代码粘贴到哪一段。\n\n"
        f"任务: {task}\n\n"
        f"结果1(state/choice 定义与识别标准): {r1}\n\n"
        f"结果6(核验标准): {r6}\n\n"
        f"state 取值 JSON（识别结果，只含 state）: {r4}\n\n"
        "当前 AI 逻辑代码（供你修改参考）：\n"
        f"【结果2 当前代码】\n{cur_r2 or "（无）"}\n\n"
        f"【结果3 当前代码】\n{cur_r3 or "（无）"}\n\n"
        "程序3 最近运行输出：\n" + (out[-2000:] if out else "（无输出）") + "\n\n"
        f"用户反馈问题：{question}\n\n"
        "请只输出你要修改的段（可以只改一段，也可以两段都改），严格按以下格式：\n"
        "【修改结果2】\n```python\n<新的结果2 完整代码>\n```\n"
        "【修改结果3】\n```python\n<新的结果3 完整代码>\n```\n"
        "禁止输出框架代码、禁止解释文字、禁止输出其他任何内容。"
    )
    emit("[反馈修正] 发送 AI 逻辑（结果2/结果3）+ 运行输出 + 用户问题 给 LLM ...")
    if mock:
        reply = MOCK_RESULTS["feedback"]
        emit("[反馈修正] （模拟模式）返回模拟反馈结果")
    else:
        reply = call_llm(cfg, [{"role": "user", "content": prompt}])
    save_text(os.path.join(mem_dir, "反馈输出.txt"), reply)
    flush_dialog_log(mem_dir, emit)  # 反馈对话也写入 LLM对话记录.txt

    # 解析段位标签：明确 LLM 修改的是哪一段、贴到哪
    parts = _parse_feedback_parts(reply)
    if not parts:
        emit("[反馈修正] 未能从 LLM 回复中解析出【修改结果2/结果3】段位标签，未修改程序3")
        emit(f"[反馈修正] LLM 原始回复: {summary(reply, 300)}")
        return
    final_r2 = parts.get("result2", cur_r2 or "")
    final_r3 = parts.get("result3", cur_r3 or "")
    # 静态校验：语法 + import 白名单（框架段不动，AI 段也要先过关）
    for label, c in (("结果2", final_r2), ("结果3", final_r3)):
        if not c.strip():
            continue
        ok, errs = validate_code_imports(strip_code_fence(c), label)
        if not ok:
            emit(f"[反馈修正] {label} 未通过校验（语法或白名单），未替换: " + "; ".join(errs))
            return
    # 只替换 AI 逻辑段（RESULT2/RESULT3/_BLOCKS 三行），框架段完全不动
    new_code = code
    if "result2" in parts:
        new_code = re.sub(r"^RESULT2 = .*$", lambda m: "RESULT2 = " + repr(final_r2), new_code, count=1, flags=re.M)
    if "result3" in parts:
        new_code = re.sub(r"^RESULT3 = .*$", lambda m: "RESULT3 = " + repr(final_r3), new_code, count=1, flags=re.M)
    _blk = strip_code_fence(final_r2) + "\n\n# ============ 模块分隔 ============\n\n" + strip_code_fence(final_r3)
    new_code = re.sub(r"^_BLOCKS = .*$", lambda m: "_BLOCKS = " + repr(_blk), new_code, count=1, flags=re.M)
    # 同步更新记忆与主程序.py 里的结果2/结果3（程序2 重跑时保持一致）
    if "result2" in parts:
        save_text(os.path.join(mem_dir, "结果2.txt"), final_r2)
    if "result3" in parts:
        save_text(os.path.join(mem_dir, "结果3.txt"), final_r3)
    mp3 = os.path.join(root_dir, "主程序.py")
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
            emit(f"[反馈修正] 同步主程序.py 失败（不影响本次程序3 修改）: {_e}")
    emit("[反馈修正] 已按段位替换 AI 逻辑（框架段未动），正在重启程序3 ...")
    save_text(py3, new_code)
    run_program3_local(cfg, root_dir, r4, emit=emit)
    emit("[执行完毕] 反馈修正完成，程序3 已重新运行")


# ---------------------------------------------------------------- 程序2 模板（主程序.py）

# ================================================================
# 程序3（运行程序.py）代码契约 —— 系统生成时写入程序3 头部，请勿手改
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
P3_CONTRACT_TEXT = '''# 程序3 代码契约（系统生成，请勿手改）
# 【不可更改】段1 头部/import · 段2 batch_util · 段3 信息常量 · 段4 输出辅助 · 段5 本地采集 · 段7 窗口壳 —— 系统框架
# 【可更改】段6 _BLOCKS（结果2 state→choice 运算流程 ＋ 结果3 choice 执行程序）—— 每次任务由 LLM 重新生成
# AI 代码可用：window_out / window_in / window_inputs / show_text_result / run_script_with_window / RESULT4 / COLLECT / ENV_INFO / batch_util
# 禁止：白名单外 import、阻塞式输入等待、sys.exit()、死循环、修改框架段变量'''

MAIN_PROGRAM_TEMPLATE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主程序.py —— 程序2（由 训练程序.py 自动生成，带 LLM 连接可视化界面）
任务: @@TASK@@

功能:
  - 可视化界面: 输入 API 地址 / 密钥 / 模型
  - 三个按钮：「采集 LLM 信息并运算」「采集本地信息并运算」「向 LLM 输入信息（人工调试）」
  - 将 结果4+结果2+结果3 组装生成 程序3 = 子文件夹/运行程序.py 并运行

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

IDENTIFY_PREFIX = '请你根据以下标准识别：\n并且按照json格式严格输出\n'

# 当前运行中的子进程（程序3 / 收集脚本），供「停止」按钮终止
CURRENT_PROC = [None]

# 停止请求标志：「停止」按钮置 True；run_auto 各阶段检查后提前退出
STOP_REQUESTED = [False]

MOCK_REPLY = @@MOCK4@@
MOCK_COLLECT = ('原始采集信息（通过联网搜索获取）：\n'
    '黄金价格：约 2650 美元/盎司（联网搜索：kitco/goldprice 实时报价）\n'
    '24小时涨跌幅：基本持平（联网搜索：黄金实时行情）\n'
    '美元指数：基本持平（联网搜索：美元指数行情）\n'
    '避险情绪：中性（联网搜索：地缘与市场风险消息）\n'
    '黄金ETF资金净流入：零或数据缺失（联网搜索：黄金ETF持仓数据）')
MOCK_CONNECTION_REPLY = '__CONNECTION_OK__ LLM连接成功（模拟）。'


PLATFORM_PRESETS = {
    "火山方舟/豆包": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-lite-260428",
        "note": "密钥=控制台 APIKey（UUID 风格）；model=控制台模型 ID（如 doubao-seed-2-0-lite-260428）或接入点 ID（ep- 开头），从「开通管理」复制",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "密钥以 sk- 开头",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "密钥以 sk- 开头",
    },
    "通义千问(阿里云百炼)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "密钥以 sk- 开头",
    },
    "智谱GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "note": "密钥以 数字.数字 开头（如 123.abc...）",
    },
    "Kimi(Moonshot)": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "note": "密钥以 sk- 开头",
    },
}

# 各平台常用模型名（下拉框随平台预设联动；均可手动输入任意模型 ID）
MODELS_BY_PLATFORM = {
    "火山方舟/豆包": [
        "doubao-seed-2-0-lite-260428",
        "doubao-seed-2-0-mini-260428",
    ],
    "OpenAI": ["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini", "o3-mini"],
    "DeepSeek": ["deepseek-chat", "deepseek-reasoner"],
    "通义千问(阿里云百炼)": ["qwen-plus", "qwen-max", "qwen-turbo"],
    "智谱GLM": ["glm-4-flash", "glm-4-plus", "glm-4-air"],
    "Kimi(Moonshot)": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
}

# 兼容旧版：仅用于读取旧配置时的模型名升级
COMMON_MODELS = [m for lst in MODELS_BY_PLATFORM.values() for m in lst]


def _http_hint(code, base, model):
    """把 HTTP 状态码翻译成中文排查建议。"""
    if code in (401, 403):
        return ("认证失败：① API Key 是否复制完整、首尾无空格？② 该 Key 是否与所选平台匹配"
                "（火山方舟的 Key 不能填在 OpenAI/DeepSeek 地址上）？③ 火山方舟请用控制台创建的 "
                "APIKey（UUID 格式），并在控制台开通对应模型/推理接入点。")
    if code == 404:
        return (f"地址或模型名不正确：确认接口地址路径完整，且模型名 {model!r} 在当前账号可用。"
                "火山方舟提示：模型名请从控制台「开通管理」复制小写模型 ID（如 doubao-seed-2-0-lite-260428）"
                "或推理接入点 ID（ep- 开头）；不要直接复制页面显示名（如 Doubao-Seed-2.0-lite 260428），"
                "也不要选未开通的模型。")
    if code == 429:
        return "请求过于频繁或额度不足：稍后重试，或检查账号余额/限流设置。"
    if code >= 500:
        return "平台服务端暂时故障：稍后重试。"
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
    """快速诊断 DNS 解析与 TCP 连通性，区分网络问题与接口问题。"""
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
        return (f"DNS 解析 {host} → {addrs}（{dns_t:.2f}s）；"
                f"TCP 连接 {host}:{port} 成功（{tcp_t:.2f}s）。网络层可达，"
                f"超时发生在等待 LLM 服务端返回数据阶段。")
    except Exception as e:
        return f"网络诊断：{e}（若此处报错，说明 DNS 或 TCP 连接本身就不通）"


# 本程序所有 LLM 调用（发送+接收）统一记录，运行/调试后写入 记忆/LLM对话记录.txt
LLM_DIALOG_LOG = []


def _flush_dialog_log():
    try:
        _mem = os.path.join(os.path.dirname(os.path.abspath(__file__)), "记忆")
        os.makedirs(_mem, exist_ok=True)
        _parts = []
        for _i, _d in enumerate(LLM_DIALOG_LOG, 1):
            _parts.append("【对话 %d%s】\n【发送】\n%s\n【接收】\n%s" % (
                _i, "（模拟）" if _d.get("mock") else "",
                json.dumps(_d.get("send", []), ensure_ascii=False, indent=1),
                _d.get("receive", "")))
        save_text(os.path.join(_mem, "LLM对话记录.txt"), "\n\n".join(_parts))
    except Exception:
        pass


def _open_result_file():
    """任务做完后，用系统默认程序打开 记忆/最终结果.txt，让用户直接看到结果。"""
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "记忆", "最终结果.txt")
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
ENV_INFO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "环境信息.json")


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
    """把信息输入框内容拼成可读文本（供 Prompt 注入）。"""
    return read_env_info() or "（空）"


def env_need_check(cfg, emit=print, ask_user=None):
    """采集前让 LLM 判断：按当前采集方案是否需要用户补充登录/个人资料等信息。
    需要时把 AI 的填写说明显示给用户，并（GUI 模式）等待用户填好输入栏后回车继续。"""
    try:
        _prompt = (
            "任务：" + TASK + "\n\n"
            "信息采集方案（程序1 生成）：\n" + COLLECT_PLAN + "\n\n"
            "当前用户可手动提供的信息输入框内容如下（可能为空）：\n"
            + env_info_text() + "\n\n"
            "请判断：按照上述采集方案采集信息，是否需要额外的登录、个人资料等信息"
            "（如账号、密码、授权码等凭据）？\n"
            "若不需要，只回复：不需要\n"
            "若需要，请明确说明用户需要在「信息输入」框里填什么内容，例如：\n"
            "  请在信息输入框填写：\n"
            "  账号 = 你的账号\n"
            "  密码 = 你的密码\n"
            "  （按实际需要列出每项，一行一项）\n"
            "（所有信息都填在同一个输入框，一行一项即可）"
        )
        emit("[环境信息] 让 LLM 判断采集信息是否足够 ...")
        reply = llm_ask([{"role": "user", "content": _prompt}], cfg)
        emit("[环境信息] AI 判断回复：")
        for _line in (reply or "").splitlines()[:30]:
            emit("  | " + _line)
        _need = bool(reply) and "不需要" not in reply and any(
            kw in reply for kw in ("填写", "请输入", "请提供", "请告诉", "请给出",
                                   "需要您", "需要你", "授权码", "账号", "密码", "凭据",
                                   "服务器地址", "用户名"))
        # 信息输入方式：window3=输入在程序3 窗口（需要输入时跳过窗口2 采集）；
        #              window2=输入在程序2 窗口（运行采集，采集阶段暂停等用户输入）
        if cfg.get("input_mode", "window3") != "window2" and _need:
            emit("[环境信息] AI 提示需要用户输入（如账号/密码/授权码等凭据）。")
            emit("[环境信息] 输入方式为「窗口3」：跳过程序2 采集代码，直接生成并运行程序3；输入在程序3 窗口收集。")
            return True
        return False
    except Exception as e:
        emit(f"[环境信息] 判断失败（跳过，按原方案继续采集）: {e}")
        return False


def call_llm(messages, cfg):
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    if not base.startswith("http://") and not base.startswith("https://"):
        raise RuntimeError(f"接口地址格式错误: {base!r}（应以 http:// 或 https:// 开头）")
    if not key:
        raise RuntimeError("API Key 为空：请在界面配置区粘贴 API Key，或设置环境变量 ARK_API_KEY / DOUBAO_API_KEY")
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
        payload = {
            "model": model,
            "messages": messages,
            "temperature": cfg.get("temperature", 0.2),
            "stream": False,
        }
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
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[HTTP 错误 {e.code}] {detail}", "ok": False})
        hint = _http_hint(e.code, base, model)
        raise RuntimeError(
            f"LLM 接口错误 {e.code}\n"
            f"请求地址: {url}\n"
            f"平台响应: {detail}\n"
            + (("排查建议: " + hint) if hint else ""))
    except Exception as e:
        LLM_DIALOG_LOG.append({"send": messages, "receive": f"[异常] {e}", "ok": False})
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            diag = _diag_network(url)
            prox = [p for p in ("http_proxy", "https_proxy", "all_proxy")
                    if os.environ.get(p) or os.environ.get(p.upper())]
            prox_txt = ("检测到系统代理环境变量: " + ", ".join(prox) +
                        " → 若 curl 也超时，请先临时关闭代理/VPN 再试。" if prox
                        else "未检测到系统代理环境变量。")
            curl_body = json.dumps(payload, ensure_ascii=False)[:300]
            raise RuntimeError(
                f"LLM 请求超时（已等待 {cfg.get('timeout', 280)} 秒）：可能是 Prompt 过长或网络较慢。\n"
                f"请求地址: {url}\n原始错误: {e}\n{diag}\n{prox_txt}\n"
                f"自测命令（在终端运行，替换 <你的APIKey> 后可直接判断是否接口问题）:\n"
                f"  curl -sS -m 60 {url} \\\n"
                f"    -H 'Authorization: Bearer <你的APIKey>' -H 'Content-Type: application/json' \\\n"
                f"    -d '{curl_body}'\n"
                f"判断：① curl 也超时 → 网络/代理问题；② curl 快速报 401/404 → Key 或模型名问题"
                f"（见「测试连接」按钮的排查建议）；③ curl 正常返回 → 请把「调用超时」调大到 600 秒再试。")
        raise RuntimeError(f"无法连接 LLM 接口（{base}），请检查网络或地址是否正确: {e}")
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
        raise RuntimeError(f"LLM 返回格式异常: {str(body)[:300]}")
    LLM_DIALOG_LOG.append({"send": messages, "receive": content, "ok": True})
    return content


def llm_ask(messages, cfg=None):
    cfg = cfg or CONFIG
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        text = messages[-1]["content"]
        if text.startswith("请你根据以下标准识别"):
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

def _check_ai_code(code, label="AI 代码"):
    """程序3 生成前对 AI 代码做最终复查：语法 + import 白名单。
    返回 (ok, problems)。失败则不生成程序3（避免生成必败程序）。"""
    problems = []
    for _m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M):
        _top = _m.group(1).split(".")[0]
        if _top not in _P3_ALLOWED_IMPORTS:
            problems.append(
                f"{label}: 使用了工具清单外的库 {_m.group(1)}"
                f"（仅允许内置 batch_util 与标准库，禁止第三方库）")
    try:
        compile(code, "<p3_check>", "exec")
    except SyntaxError as _e:
        problems.append(f"{label}: 语法错误 {_e}")
    return (not problems), problems


def save_text(path, text):
    # utf-8-sig 带 BOM，Windows 记事本直接打开不乱码
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text or "")


def decode_bytes(b):
    """子进程输出按 utf-8 → gbk → latin-1 依次尝试解码，避免 Windows 下乱码。"""
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
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "记忆", "密钥.txt")


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
    path = os.path.join(folder, "主程序配置.json")
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
    with open(os.path.join(folder, "主程序配置.json"), "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def collect_info(cfg, emit=print, ask_user=None, mode="auto"):
    """【信息收集】两步：
    第一步 收集：按训练生成的收集方案执行——有脚本则运行脚本拿真实原始信息，否则发 Prompt 给 LLM 收集；
    第二步 转换：把原始信息交给 LLM，转成严格 JSON，只标每个 state 的当前取值（不含 choice）。
    如果收集到的信息表明需要用户补充输入（如"请输入..."），会暂停并调用 ask_user(问题) 等待用户回答。
    mode: "auto"=有脚本走脚本、否则 LLM；"local"=强制本地脚本采集（无脚本则报错）；
          "llm"=强制 LLM 收集（跳过脚本分支）。
    返回 state 取值 JSON 字符串；失败返回 None。"""
    folder = os.path.dirname(os.path.abspath(__file__))
    sub_dir = os.path.join(folder, "子文件夹")
    mem_dir = os.path.join(folder, "记忆")
    os.makedirs(sub_dir, exist_ok=True)
    os.makedirs(mem_dir, exist_ok=True)

    # ---- 检测是否需要用户补充信息 ----
    def needs_user_input(text):
        """检测是否需要用户补充输入。
        只检测 LLM 采集回复里明确的"请用户输入"指令；
        程序抓取的网页内容属于数据，不参与判定（网页 HTML 里 ?/请输入 到处都是）。"""
        if not text:
            return False
        # 切掉【程序抓取的网页内容】及之后的部分：网页 HTML 里的 ?/请输入 到处都是，不能参与判定
        idx = text.find("【程序抓取的网页内容")
        if idx >= 0:
            text = text[:idx]
        # 再切掉网页正文段（LLM 回复常以『网页内容：…』之类结尾）
        idx2 = text.find("网页内容")
        if 0 <= idx2 < len(text) and idx2 > 50:
            text = text[:idx2]
        patterns = ["请输入", "请提供", "请告诉我", "请给出", "请填写", "请选择", "请告知", "请补充"]
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
    emit("[收集·LLM] 按收集方案命令 LLM 采集相关信息 ...")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # 直接粘贴完整的收集方案原文（不用 JSON 提取，兼容文字描述格式）
    plan_note = COLLECT_PLAN if COLLECT_PLAN else "（收集方案未给出，按结果1 的 state 定义采集）"
    collect_prompt_text = (
        f"请为任务「{TASK}」采集完成任务所需的相关信息。\n"
        f"当前系统时间：{now}\n\n"
        "以下是 state 定义与识别标准（结果1）：\n" + RESULT1 + "\n\n"
        "信息收集方案（结果2）：\n" + plan_note + "\n\n"
        "请按方案逐项采集每个 state 当前真实取值所需的信息（实时数据、用户输入、"
        "环境数据、业务数据等），说明信息来源与获取方式，并给出最可能/最新的原始信息。\n"
        "不要输出 JSON，直接输出采集到的原始信息（文本或键值列表均可）。\n\n"
        "环境信息（用户已提供，可能为空；涉及登录/凭据时使用）：\n" + env_info_text() + "\n\n"
        "【重要】你已开启联网搜索能力（web_search），需要实时/外部数据（天气、行情、网页信息等）的 state，\n"
        "请直接用联网搜索获取真实最新信息，说明信息来源即可；\n"
        "不要声明 URL、不要要求本地程序抓取网页。\n"
        + BUILTIN_DATA_URLS +
        "（也可声明其他公开 JSON 接口。）\n"
    )
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        emit("[收集·LLM] （模拟模式）使用模拟采集结果")
    raw = llm_ask([{"role": "user", "content": collect_prompt_text}], cfg)
    emit("[收集·LLM] 原始采集信息: " + summary(raw, 400))

    # ---- 联网搜索模式：不本地抓取网页；LLM 通过 web_search 直接获取实时信息 ----
    if re.search(r"FETCH_URLS\s*开始", raw or ""):
        emit("[收集·LLM] 提示：当前为联网搜索模式，不本地抓取网页；已忽略 LLM 声明的 URL，由 LLM 联网搜索获取信息。")

    # ---- 检测是否需要用户补充信息，暂停等待 ----
    # ---- 需要用户补充输入：按「信息输入方式」分流 ----
    # window2：在程序2 窗口暂停等待用户输入后继续转换；window3：跳过窗口2 暂停，占位继续，输入在程序3 窗口
    if needs_user_input(raw):
        if cfg.get("input_mode", "window3") == "window2" and ask_user is not None:
            emit("[收集] 检测到需要用户补充信息（输入方式为「窗口2」），暂停等待 ...")
            question = raw.strip()[:500] if raw.strip() else "请提供完成任务所需的补充信息"
            emit(f"[收集] 问题: {question}")
            user_answer = ask_user(question)
            if user_answer and user_answer.strip():
                emit(f"[收集] 用户已补充信息: {summary(user_answer, 200)}")
                raw = raw.strip() + "\n\n【用户补充】" + user_answer.strip()
            else:
                emit("[收集] 用户未补充信息，继续使用原始信息 ...")
        else:
            emit("[收集] 检测到需要用户补充输入（如登录凭据/参数）；当前无暂停等待输入的条件"
                  "（输入方式为「窗口3」，或本环境没有输入界面），不再在程序2 窗口等待。")
            emit("[收集] 将先生成并运行程序3；需要输入时程序3 窗口会提示您填写（如账号/密码/授权码等凭据）。")
            emit("[收集] AI 提示需补充: " + summary(raw, 300))
            placeholder = {s: "待程序3 运行时由用户输入" for s in expected_states}
            state_json = json.dumps(placeholder, ensure_ascii=False)
            save_text(os.path.join(mem_dir, "原始采集信息.txt"), raw)
            # 占位结果不写入 识别结果.txt 缓存：避免下次运行误复用占位而跳过采集
            emit("[收集] 已用占位 state 取值继续（程序3 运行时收集真实输入）: " + summary(state_json, 200))
            return state_json

    save_text(os.path.join(mem_dir, "原始采集信息.txt"), raw)

    # ---- 第二步：转换（LLM 把原始信息转成每个 state 取值 JSON，只含 state，不含 choice）----
    emit("[转换] 把原始采集信息交给 LLM，转换为 state 取值 JSON ...")
    convert_prompt = (
        "请你根据以下标准识别当前任务的实际状态，并且按照 json 格式严格输出 " + RESULT1 + "：\n"
        "以下是采集到的原始信息与联网搜索获取的内容（真实数据，供你识别 state 取值）：\n"
        + (raw or "（无原始信息）") + "\n\n"
        "若你已开启联网搜索能力（web_search），且上述信息不足，可直接联网搜索补充后再判断。\n"
        "请基于上述信息判断当前各 state 的取值（信息不足的部分可依据常识合理推断）。\n"
        "标明每一个 state 的当前取值。 "
        "【重要】必须每一个 state 都要有取值，不能遗漏任何一个 state。 "
        "只输出 state 的取值，不要输出 choice（choice 是后续程序执行的动作）。 "
        "【输出必须是扁平 JSON】直接 state 名 → 取值，禁止包 need_loop 或 states 外壳，禁止嵌套。 "
        '格式示例：{"<state名称>": "<当前取值>", ...}（必须使用上方识别标准中定义的确切state名称，禁止臆造或照抄其他名字）。 '
        "禁止任何解释文字、markdown 标记或代码块围栏。"
    )
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        emit("[转换] （模拟模式）使用模拟 state 取值 JSON")
    # 独立发送：只发当前 Prompt + 粘贴原始信息，不用对话历史
    state_json = llm_ask([{"role": "user", "content": convert_prompt}], cfg)

    # ---- 第三步：JSON 格式校验 + 完整性校验（不符合自动重发，最多 3 次）----
    emit("[校验] 正在校验 LLM 回复是否符合 JSON 格式，且每个 state 都有取值 ...")

    def flatten_state_json(parsed):
        """兼容嵌套包裹格式：{"need_loop":..., "states": {"名": "值"}} → 扁平 {"名": "值"}。"""
        if not isinstance(parsed, dict):
            return parsed
        _st = parsed.get("states")
        if isinstance(_st, dict):
            parsed = dict(_st)
        elif isinstance(_st, list):
            _flat = {}
            for _item in _st:
                if isinstance(_item, dict) and _item.get("name"):
                    _flat[_item["name"]] = (_item.get("value") or _item.get("取值")
                                            or (_item.get("values") or [""])[0] if isinstance(_item.get("values"), list) else _item.get("values"))
            if _flat:
                parsed = _flat
        parsed.pop("need_loop", None)
        return parsed

    def validate_state_json(text):
        """校验 JSON 格式 + 所有 state 都有取值。返回 (ok, missing_states, parsed)。"""
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
        emit(f"[校验 {retry_count}/3] 校验不通过，自动要求 LLM 重答 ...")
        if missing == expected_states and parsed is None:
            # JSON 格式不对
            emit(f"[校验 {retry_count}/3] 原因：不是有效 JSON 格式")
            retry_prompt = (
                "请严格按照json格式输出。\n\n"
                "state 定义（结果1）：\n" + RESULT1 + "\n\n"
                "你刚才的回复不是严格的 JSON 格式。请重新输出：\n"
                "1. 只输出一个 JSON 对象，不要任何解释、说明、markdown 围栏或代码块标记\n"
                '2. 格式必须是：{"state1": "<取值>", "state2": "<取值>", ...}\n'
                "3. 取值用字符串，不要嵌套对象或数组\n"
                "4. 只包含 state，不要包含 choice\n"
                "5. 必须包含所有 state，不能遗漏\n"
                "6. 需要实时数据（天气等）时，直接用你的联网搜索能力获取真实最新信息，不要声明 URL。\n\n"
                "你刚才的回复：\n" + state_json
            )
        else:
            # JSON 格式对，但缺了某些 state
            emit(f"[校验 {retry_count}/3] 原因：缺少 state 取值: {', '.join(missing)}")
            retry_prompt = (
                "state 定义（结果1）：\n" + RESULT1 + "\n\n"
                "你刚才的回复 JSON 格式正确，但缺少以下 state 的取值："
                + ", ".join(missing) + "\n"
                "请重新输出完整的 JSON，必须包含每一个 state 的取值，不能遗漏任何一个。\n"
                "缺少取值可能是实时信息没拿到：请用你的联网搜索能力（web_search）直接搜索获取真实最新信息，不要声明 URL。\n"
                '格式：{"state1": "<取值>", "state2": "<取值>", ...}\n\n'
                "你刚才的回复：\n" + state_json
            )
        # 独立发送：只发当前重试 Prompt
        state_json = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
        emit(f"[校验 {retry_count}/3] LLM 重答: " + summary(state_json, 300))

    ok, missing, parsed = validate_state_json(state_json)
    if ok:
        state_json = json.dumps(parsed, ensure_ascii=False)   # 拍平后重写，保证下游格式一致
        emit("[校验] JSON 格式校验通过，且所有 state 都有取值！进入下一步 ...")
    else:
        reason = "不是有效 JSON 格式" if not parsed else f"缺少 state 取值: {', '.join(missing)}"
        emit(f"[校验] 3 次校验均未通过（{reason}），需要用户补充信息 ...")

    # ---- 3 次仍失败：暂停让用户选择处理方式 ----
    if not ok and ask_user is not None:
        emit("[校验] 3 次校验均未通过，暂停等待用户处理 ...")
        emit(f"[校验] LLM 实际输出内容: {summary(state_json, 500)}")
        emit(f"[校验] 出现问题: {reason}")
        question = (
            "LLM输出内容为：\n" + summary(state_json, 300) + "\n\n"
            f"出现问题: {reason}\n\n"
            "请选择您的信息采集方案：\n"
            "1. 点「AI 随机生成」：让 LLM 自动生成随机的 state 取值\n"
            "2. 点「AI 完善采集流程」：让 LLM 改进信息收集方案后重新采集\n"
            "3. 手动输入：直接填写正确 JSON（如 {\"state1\": \"晴天\"}）或补充信息\n"
            "（或返回程序一进行反馈修正）"
        )
        user_input = ask_user(question)
        if user_input and user_input.strip():
            user_input = user_input.strip()
            if user_input == "随机" or user_input == "random":
                emit("[转换] 用户选择随机生成，命令 LLM 生成随机 state 取值 ...")
                rand_prompt = (
                    "请根据以下 state 定义，为每个 state 生成一个合理的随机取值，"
                    "并严格输出 JSON 格式（只含 state，不含 choice）：\n\n" + RESULT1 +
                    "\n\n只输出 JSON 对象，不要任何解释或 markdown 标记。"
                )
                state_json = llm_ask([{"role": "user", "content": rand_prompt}], cfg)
                emit("[转换] AI 随机生成: " + summary(state_json, 300))
            elif user_input == "完善" or user_input == "improve":
                emit("[转换] 用户选择完善采集流程，命令 LLM 改进收集方案 ...")
                improve_prompt = (
                    "之前的信息收集方案不够完善，导致无法得到有效的 state 取值。\n"
                    "请根据任务「" + TASK + "」和 state 定义，重新设计一个更完善的信息收集方案，"
                    "确保每个 state 的取值都能被准确采集。\n\n"
                    "state 定义：\n" + RESULT1 + "\n\n"
                    "之前的原始信息：\n" + raw + "\n\n"
                    "请用 JSON 输出新的收集方案，格式：\n"
                    '{"states_collect": [{"state": "state1", "method": "prompt", "source": "更明确的信息来源"}, ...]}\n'
                    "只输出 JSON，不要解释。"
                )
                new_plan = llm_ask([{"role": "user", "content": improve_prompt}], cfg)
                emit("[转换] LLM 改进方案: " + summary(new_plan, 300))
                # 重新采集（用新方案）
                plan = extract_json(new_plan) or {}
                states_collect = plan.get("states_collect") or []
                desc = []
                for item in states_collect:
                    if isinstance(item, dict):
                        desc.append(f"- state「{item.get('state')}」来源: {item.get('source') or '未知'}")
                plan_note = "\n".join(desc) if desc else ""
                recollect_prompt = (
                    f"请按以下改进后的收集方案，为任务「{TASK}」采集每个 state 的当前取值所需信息。\n\n"
                    "收集方案：\n" + plan_note + "\n\n"
                    "state 定义：\n" + RESULT1 + "\n\n"
                    "直接输出原始信息，不要 JSON。"
                )
                raw = llm_ask([{"role": "user", "content": recollect_prompt}], cfg)
                emit("[转换] 重新采集到: " + summary(raw, 300))
                # 再转换一次
                convert_prompt2 = (
                    IDENTIFY_PREFIX + RESULT1 + "\n\n当前采集到的原始信息：\n" + raw +
                    "\n\n请根据识别标准，把原始信息转换成严格的 JSON，标明每一个 state 的当前取值。"
                    "若你已开启联网搜索能力（web_search）且信息不足，可直接联网搜索补充。"
                    "只输出 JSON 对象，不要任何解释或 markdown 标记。"
                )
                state_json = llm_ask([{"role": "user", "content": convert_prompt2}], cfg)
                emit("[转换] 改进后转换结果: " + summary(state_json, 300))
            elif user_input.startswith("{") and extract_json(user_input):
                state_json = user_input
                emit("[转换] 用户直接提供了正确 JSON")
            else:
                emit(f"[转换] 用户补充信息: {summary(user_input, 200)}，再让 LLM 转换 ...")
                raw_with_user = raw.strip() + "\n\n【用户补充】" + user_input
                retry_prompt = (
                    "请根据识别标准和用户补充信息，把信息转换成严格的 JSON，标明每一个 state 的当前取值。"
                    "只输出 JSON 对象，不要任何解释或 markdown 标记。\n\n" +
                    "识别标准：\n" + RESULT1 + "\n\n" +
                    "信息：\n" + raw_with_user
                )
                state_json = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
                emit("[转换] LLM 转换结果: " + summary(state_json, 300))
        else:
            emit("[转换] 用户未选择/输入，继续使用原始结果 ...")

    save_text(os.path.join(mem_dir, "识别结果.txt"), state_json)
    emit("[转换] state 取值 JSON 已保存到 记忆/识别结果.txt: " + summary(state_json, 300))
    sj = extract_json(state_json)
    if sj is None:
        emit("[转换] 警告：最终仍未解析为 JSON，已保存原文，请用程序1 反馈修正。")
    return state_json


def run_auto(cfg, emit=print, popup=False, ask_user=None, force_mode=None):
    """force_mode: None=自动（LLM 采集/复用）；
    "llm"=强制 LLM 收集信息并运算；"local"=强制本地脚本采集并运算。"""
    STOP_REQUESTED[0] = False  # 每次运行前重置停止标志（「停止」按钮置 True 后各阶段提前退出）
    folder = os.path.dirname(os.path.abspath(__file__))
    sub_dir = os.path.join(folder, "子文件夹")
    mem_dir = os.path.join(folder, "记忆")
    os.makedirs(sub_dir, exist_ok=True)
    os.makedirs(mem_dir, exist_ok=True)

    # 第〇步：让 LLM 判断采集信息是否足够；需要用户输入时跳过程序2 采集，交给程序3 窗口收集
    need_user_input = env_need_check(cfg, emit=emit, ask_user=ask_user)

    # 第一步：信息收集（完全靠 LLM 采集；需要用户输入 → 占位继续，输入在程序3 窗口收集）

    if STOP_REQUESTED[0]:
        emit("[停止] 已收到停止请求，不开始收集")
        return -1

    if need_user_input:
        emit("[收集] 信息需要用户输入（凭据/参数等），跳过程序2 采集代码，直接生成并运行程序3。")
        _expected = set()
        try:
            _r1j = extract_json(RESULT1) or {}
            for _s in (_r1j.get("states") or []):
                if isinstance(_s, dict) and _s.get("name"):
                    _expected.add(_s["name"])
        except Exception:
            pass
        r4 = json.dumps({_s: "待程序3 运行时由用户输入" for _s in _expected}, ensure_ascii=False)
        # 占位结果不写入 识别结果.txt 缓存：避免下次运行误复用占位而跳过采集
        emit("[收集] 已用占位 state 取值继续（程序3 运行时收集真实输入）: " + summary(r4, 200))
    elif force_mode == "llm":
        emit("[收集] 强制 LLM 收集信息（不运行本地脚本）...")
        r4 = collect_info(cfg, emit, ask_user=ask_user, mode="llm")
        if r4 is None:
            emit("[错误] LLM 信息收集失败，不启动程序三。")
            return -1
    elif cfg.get("input_mode", "window3") == "window2":
        emit("[收集] 输入方式为「窗口2」：强制运行窗口2 采集（不复用旧结果）...")
        r4 = collect_info(cfg, emit, ask_user=ask_user, mode="auto")
        if r4 is None:
            emit("[错误] 信息收集失败，不启动程序三。")
            return -1
    else:
        rec_path = os.path.join(mem_dir, "识别结果.txt")
        if os.path.exists(rec_path):
            with open(rec_path, "r", encoding="utf-8-sig") as f:
                r4 = f.read()
            emit("[收集] 复用已转换的 state 取值 JSON: " + summary(r4, 300))
        else:
            r4 = collect_info(cfg, emit, ask_user=ask_user, mode="auto")
            if r4 is None:
                emit("[错误] 信息收集失败，不启动程序三。")
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
            return False, "不是有效 JSON 格式", parsed
        parsed = flatten_state_json(parsed)
        missing = [s for s in expected_states if s not in parsed]
        if missing:
            return False, f"缺少 state 取值: {', '.join(missing)}", parsed
        return True, "", parsed

    r4_valid, r4_reason, r4_parsed = validate_r4(r4)
    retry = 0
    while not r4_valid and retry < 3:
        retry += 1
        emit(f"[校验 {retry}/3] 校验不通过（{r4_reason}），自动给 LLM 发 prompt 要求严格按格式输出 ...")
        retry_prompt = (
            "请严格按照json格式提供每一个state和取值。你之前输出的结果有问题：" + r4_reason + "\n\n"
            "要求：\n"
            "1. 只输出一个 JSON 对象，不要任何解释或 markdown 标记\n"
            "2. 必须包含所有 state，每个 state 都要有取值\n"
            "3. 格式：{\"state1\": \"<取值>\", \"state2\": \"<取值>\", ...}\n"
            "4. 需要实时数据（天气等）时，请用你的联网搜索能力（web_search）直接搜索获取真实最新信息，不要声明 URL。\n\n"
            "之前的输出：\n" + summary(r4, 500) + "\n\n"
            "state 定义：\n" + RESULT1 + "\n\n"
            "请重新输出严格 JSON。"
        )
        r4 = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
        emit(f"[校验 {retry}/3] LLM 重答: " + summary(r4, 300))
        r4_valid, r4_reason, r4_parsed = validate_r4(r4)

    if not r4_valid:
        emit(f"[错误] 3 次重试后校验仍不通过（{r4_reason}），不启动程序三。")
        emit(f"[错误] LLM 实际反馈内容: {summary(r4, 800)}")
        return -1

    emit("[校验] state 取值 JSON 校验通过（格式正确 + 所有 state 都有取值），启动程序三 ...")

    # 第三步：组装 → 生成 运行程序.py（程序3）= 结果2(运算程序) + 结果3(执行程序+窗口代码)
    emit("[主程序] 组装 结果2(运算程序) + 结果3(执行程序+窗口代码) ...")
    blocks = [strip_code_fence(x) for x in (RESULT2, RESULT3)]
    blocks = [b for b in blocks if b]
    # AI 代码最终复查：语法 + import 白名单（通过才生成程序3，避免生成必败程序）
    for _b in blocks:
        _ok, _errs = _check_ai_code(_b)
        if not _ok:
            emit("[错误] AI 代码最终复查未通过，不启动程序三: " + "; ".join(_errs))
            return -1
    emit("[校验] AI 代码最终复查通过（语法 + import 白名单）")
    context = (
        "# -*- coding: utf-8 -*-\n"
        "# 自动生成程序（程序3，由 主程序.py 自动生成）\n"
        "# 任务: " + TASK + "\n"
        "\n"
        + P3_CONTRACT_TEXT + "\n"
        "import threading\n"
        "import json\n"
        "import os\n"
        "\n"
        "# ---- 内置工具 batch_util（程序自动注入，无需 pip 安装）----\n"
        + BATCH_UTIL_SOURCE + "\n"
        "# ---- 程序1 相关信息（完整保留，程序3 独立可用，不丢失）----\n"
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
        "# ---- 输出辅助（全自动运行：不弹窗、不等待点击，全部 print 输出）----\n"
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
        "        print('【决策·文字】', str(msg))\n"
        "if \"run_script_with_window\" in _win_ns:\n"
        "    run_script_with_window = _win_ns[\"run_script_with_window\"]\n"
        "else:\n"
        "    def run_script_with_window(code, title=\"正在执行脚本\"):\n"
        "        try:\n"
        "            exec(compile(code, \"<script>\", \"exec\"), {\"__name__\": \"__main__\", \"RESULT4\": RESULT4})\n"
        "            print('【决策·脚本】执行完成')\n"
        "        except Exception as e:\n"
        "            print('【决策·脚本】执行失败:', e)\n"
        "\n"
        "# ---- 本程序由 LLM 信息采集路径生成：RESULT4 为 LLM 采集并转换的 state 取值 JSON；程序3 不采集、只输出 JSON 决策并调用脚本/生成结果文件 ----\n"
        "\n"
        "# ---- AI 生成的核心代码（结果2 运算流程 + 结果3 执行程序）----\n"
        "_BLOCKS = " + repr("\n\n# ============ 模块分隔 ============\n\n".join(blocks)) + "\n"
        "\n"
        + WINDOW_SHELL_SOURCE + "\n"
    )
    program = context
    py3 = os.path.join(sub_dir, "运行程序.py")
    save_text(py3, program)
    emit("[主程序] 程序3 已生成: " + py3)

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
        emit("[主程序] 该任务需要循环执行（持续监控/实时数据更新类）")
    else:
        emit("[主程序] 该任务为一次性任务，只执行一次")

    loop_count = 0
    max_loops = 3  # 最多循环 3 次，避免无限循环
    while True:
        if STOP_REQUESTED[0]:
            emit("[停止] 已收到停止请求，流程中止")
            break
        rc = run_program3(cfg, emit=emit, popup=popup,
                          extra_env={"MAIN_RESULT4": r4})
        loop_count += 1
        emit(f"[执行完毕] 程序3 第 {loop_count} 轮运行完成")

        # 检查程序3 输出是否为空
        output_path = os.path.join(mem_dir, "运行输出.txt")
        if os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8-sig") as f:
                output_text = f.read().strip()
            if not output_text:
                emit("[错误] 程序3 输出为空！请返回程序1 输入问题并点击「发送反馈并修正」")
                break

        # 不需要循环，或已到最大次数，就结束
        if not need_loop:
            emit("[执行完毕] 一次性任务，执行结束")
            break
        if loop_count >= max_loops:
            emit(f"[执行完毕] 已完成 {max_loops} 轮，循环结束（如需更多轮次，请重新点击「运行」）")
            break

        emit(f"[循环] 第 {loop_count + 1} 轮：重新采集实时信息 ...")
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
            emit(f"[循环·校验 {retry}/3] {r4_reason}，自动给 LLM 重发 prompt ...")
            retry_prompt = (
                "请严格按照json格式提供每一个state和取值。你之前输出的结果有问题：" + r4_reason + "\n\n"
                "要求：\n1. 只输出 JSON 对象，不要解释或 markdown 标记\n"
                "2. 必须包含所有 state，每个 state 都要有取值\n"
                "3. 格式：{\"state1\": \"<取值>\", ...}\n\n"
                "之前的输出：\n" + summary(r4, 500) + "\n\n"
                "state 定义：\n" + RESULT1 + "\n\n请重新输出严格 JSON。"
            )
            r4 = llm_ask([{"role": "user", "content": retry_prompt}], cfg)
            r4_valid, r4_reason, r4_parsed = validate_r4(r4)
        if not r4_valid:
            emit(f"[循环] 第 {loop_count + 1} 轮：3 次重试后校验仍不通过（{r4_reason}），停止循环")
            emit(f"[循环] LLM 实际反馈: {summary(r4, 500)}")
            break
        emit(f"[循环] 第 {loop_count + 1} 轮：校验通过，运行程序3 ...")
    _flush_dialog_log()  # 运行结束后把全部 LLM 对话（发送+接收）写入 记忆/LLM对话记录.txt
    if popup:
        _open_result_file()  # GUI 运行：任务做完后用系统默认程序打开 记忆/最终结果.txt
    return rc


def test_connection(cfg, emit=print):
    """可视化界面里的「测试连接 LLM」：真实发送带校验标记的消息，收到含标记回复才算成功。"""
    def _mask_key(k):
        k = (k or "").strip()
        return (k[:4] + "****" + k[-4:]) if len(k) > 8 else ("*" * len(k))
    # 测试连接用短超时（最多60秒）：快速失败，避免服务器hang时用户干等180秒"没有反应"
    test_cfg = dict(cfg)
    test_cfg["timeout"] = 60
    emit(f"[连接测试] 真实发送测试消息 → {cfg.get('base_url')}/chat/completions | "
         f"模型: {normalize_model_name(cfg.get('model'), cfg.get('base_url'))} | Key: {_mask_key(cfg.get('api_key'))} ...")
    emit("[连接测试] 正在等待 LLM 响应（最多 60 秒），请稍候 ...")
    if os.environ.get("MAIN_PROGRAM_MOCK"):
        reply = MOCK_CONNECTION_REPLY
        emit("[连接测试] （模拟模式）返回模拟回复")
    else:
        try:
            reply = call_llm([{"role": "user",
                               "content": "1"}], test_cfg)
        except Exception as e:
            emit(f"[连接测试] 失败：{e}")
            emit("[连接测试] 排查建议：")
            emit("  1. 如果是超时：pro 模型是推理模型，响应较慢，可以把「调用超时」调大到 300 秒再试")
            emit("  2. 如果是 401/认证失败：检查 API Key 是否复制完整、首尾无空格")
            emit("  3. 如果是 404/模型不存在：到火山方舟控制台「开通管理」确认该模型已开通，或复制推理接入点 ID（ep- 开头）")
            raise
    emit("[连接测试] LLM 原始回复全文:")
    for line in (reply or "").splitlines()[:20]:
        emit("  | " + line)
    if reply and reply.strip():
        emit("[连接测试] 成功：LLM 已返回内容，连接正常。")
    else:
        emit("[连接测试] 警告：LLM 返回为空。")
    folder = os.path.dirname(os.path.abspath(__file__))
    rec = os.path.join(folder, "记忆", "连接测试.txt")
    os.makedirs(os.path.dirname(rec), exist_ok=True)
    save_text(rec, reply)
    emit("[连接测试] 回复已储存: " + rec)
    return reply


def flatten_state_json(parsed):
    """兼容嵌套包裹格式：{"need_loop":..., "states": {"名": "值"}} → 扁平 {"名": "值"}。"""
    if not isinstance(parsed, dict):
        return parsed
    _st = parsed.get("states")
    if isinstance(_st, dict):
        parsed = dict(_st)
    elif isinstance(_st, list):
        _flat = {}
        for _item in _st:
            if isinstance(_item, dict) and _item.get("name"):
                _flat[_item["name"]] = (_item.get("value") or _item.get("取值")
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
    """文字反馈（全自动模式：不弹窗，直接打印，避免阻塞等待点击）。"""
    print("【决策·文字】", str(msg))


def run_script_window(code, title="正在执行脚本"):
    """脚本反馈（全自动模式：不弹可视化窗口，直接执行并打印，避免等待点击关闭）。"""
    try:
        exec(compile(code, "<feedback_script>", "exec"), {"__name__": "__main__"})
        print("【决策·脚本】执行完成")
    except Exception as e:
        print("【决策·脚本】执行失败:", e)


def _save_final_result(txt):
    """把最终结果写入 记忆/最终结果.txt，方便用户直接查看。"""
    try:
        folder = os.path.dirname(os.path.abspath(__file__))
        mem_dir = os.path.join(folder, "记忆")
        os.makedirs(mem_dir, exist_ok=True)
        with open(os.path.join(mem_dir, "最终结果.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
    except Exception:
        pass


def extract_decision_json(text):
    """从程序3 输出中优先提取含 "decision" 字段的 JSON 决策对象。
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
    """处理 程序3 输出的 JSON 决策：文字消息→告知用户；脚本→自动执行。"""
    text = (out or "").strip()
    if not text:
        emit("[决策] 程序3 无输出")
        emit("[最终结果] 程序3 运行无输出（请通过程序2「向 LLM 输入信息」反馈修正）")
        _save_final_result("程序3 运行无输出（请通过程序2「向 LLM 输入信息」反馈修正）。")
        return
    decision = extract_decision_json(text)
    if decision is None:
        emit("[决策] 程序3 输出如下（未解析为 JSON）：")
        for line in text.splitlines()[-30:]:
            emit("  | " + line)
        emit("[最终结果] " + summary(text, 300))
        _save_final_result("程序3 输出（未按 JSON 决策格式，以下为原样输出）：\n" + text)
        return
    emit("[决策] 程序3 决策: " + summary(json.dumps(decision, ensure_ascii=False), 400))
    d = decision.get("decision", decision)
    if isinstance(d, str):
        emit("[决策·文字] " + d)
        emit("[最终结果] " + d)
        _save_final_result("文字结果：" + d)
        if popup:
            emit(("_feedback", "text", d))
        return
    if isinstance(d, dict):
        for key in ("message", "text", "content", "msg"):
            if d.get(key):
                msg = str(d[key])
                emit("[决策·文字] " + msg)
                emit("[最终结果] " + msg)
                _save_final_result("文字结果：" + msg)
                if popup:
                    emit(("_feedback", "text", msg))
                return
        script = d.get("script") or d.get("command") or d.get("action")
        if script:
            emit("[决策·脚本] 执行: " + str(script)[:200])
            if popup:
                # 全自动：由主程序后台线程自动执行脚本，不弹窗等待点击
                emit(("_feedback", "script", str(script)))
                emit("[最终结果] 已自动执行脚本操作（结果见日志）")
                _save_final_result("脚本操作已自动执行，详见日志输出。")
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
                            _exec_out = "[脚本执行失败] " + str(_e)
                        _final_txt = "脚本执行输出：" + (_exec_out or "（无输出）") + \
                                     "\n决策：" + json.dumps(decision, ensure_ascii=False)
                        emit("[决策·脚本] 执行完成")
                        emit("[最终结果] " + summary(_final_txt, 300))
                        _save_final_result(_final_txt)
                    elif isinstance(script, str) and (
                            re.match(r"^(?:[A-Za-z]:[\\/]|/|\\./|~/|[A-Za-z0-9_.-]+\\.(?:py|exe|bat|sh|cmd))",
                                      script.strip()) or re.search(r"[;&|>]", script)):
                        subprocess.run(str(script), shell=True,
                                       timeout=cfg.get("run_timeout", 120))
                        emit("[决策·脚本] 执行完成")
                        emit("[最终结果] 脚本操作执行完成")
                        _save_final_result("脚本操作执行完成。")
                    else:
                        # 不是代码也不是可执行命令/路径：视为文字消息，不执行
                        _msg_txt = str(script).strip()
                        emit("[决策·文字] " + _msg_txt)
                        emit("[最终结果] " + _msg_txt)
                        _save_final_result("文字结果：" + _msg_txt)
                except Exception as e:
                    emit(f"[决策·脚本] 执行失败: {e}")
            return
    emit("[决策] 决策内容: " + summary(json.dumps(decision, ensure_ascii=False), 500))


def run_program3(cfg, emit=print, popup=False, extra_env=None):
    """运行 子文件夹/运行程序.py，捕获输出并处理 JSON 决策。返回退出码。"""
    folder = os.path.dirname(os.path.abspath(__file__))
    sub_dir = os.path.join(folder, "子文件夹")
    mem_dir = os.path.join(folder, "记忆")
    py3 = os.path.join(sub_dir, "运行程序.py")
    emit("[主程序] 运行 程序3 ...")
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
                emit("[程序3] 运行超时，已终止")
            out_b, err_b = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_b, err_b = proc.communicate()
            emit("[主程序] 程序3 运行超时，已终止")
        finally:
            CURRENT_PROC[0] = None
        out = (decode_bytes(out_b) or "") + \
              ("\n" + decode_bytes(err_b) if err_b else "")
        save_text(os.path.join(mem_dir, "运行输出.txt"), out)
        emit("[主程序] 程序3 退出码: " + str(proc.returncode))
        emit(out[-4000:] if out.strip() else "（无输出）")
        handle_decision(out, cfg, emit=emit, popup=popup)
        return proc.returncode
    except Exception as e:
        emit(f"[主程序] 运行程序3 失败: {e}")
        return -1


# 程序3 内置窗口壳：可输入可输出，与 AI 生成代码对接。
# - window_out(text)：AI 代码输出（GUI 显示到窗口；静默模式走 print）
# - window_in(prompt)：AI 代码请求用户输入一般信息（GUI 弹输入框等待提交；静默模式走 input）
# - window_inputs()：AI 代码读取用户填写的登录凭据（账号/密码/授权码 等输入框，
#   填给程序自己用，不发回 LLM；GUI 下会阻塞等待用户点「提交信息」；静默模式返回空串）
# - print 自动重定向到窗口；input 自动重定向到窗口输入框
# - P3_NO_GUI=1（程序2 内部自动运行）时静默执行，不弹窗口
WINDOW_SHELL_SOURCE = (
    "# ---- 程序3 内置窗口（可输入可输出，与 AI 代码对接）----\n"
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
    "def window_in(prompt='请输入：'):\n"
    "    if _io['gui'] is None:\n"
    "        # 静默模式（程序2 内部运行）：不能阻塞等待输入，自动返回空串避免挂死超时\n"
    "        try:\n"
    "            _sys.stderr.write('[程序3·静默] AI 请求输入，已自动返回空串: ' + str(prompt) + '\\n')\n"
    "        except Exception:\n"
    "            pass\n"
    "        return ''\n"
    "    _io['in_val'][0] = None\n"
    "    _io['in_evt'].clear()\n"
    "    _io['out_q'].put('\\n【需要你输入】' + str(prompt) + '（请在下方输入框填写后点击「提交」）')\n"
    "    while _io['in_val'][0] is None:\n"
    "        if _io['done'][0]:\n"
    "            return ''\n"
    "        _io['in_evt'].wait(timeout=0.2)\n"
    "    return _io['in_val'][0]\n"
    "\n"
    "def _load_required_inputs():\n"
    "    # 从 AI 代码顶层提取 REQUIRED_INPUTS = [\"标签1\", ...]（最多5个，纯字符串列表）；未声明返回 []\n"
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
    "    _io['out_q'].put('\\n【需要参数】请在下方参数输入框填写（标签由程序定义），然后点击「提交参数」。'\n"
    "                     '这些参数只给程序自己用。')\n"
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
    "        # 窗口兜底显示最终结果：无论 AI 代码是否 print，都把 记忆/最终结果.txt 内容显示到窗口\n"
    "        try:\n"
    "            _res_f = _os.path.join(_os.getcwd(), '记忆', '最终结果.txt')\n"
    "            if _os.path.exists(_res_f):\n"
    "                with open(_res_f, 'r', encoding='utf-8-sig', errors='ignore') as _rf:\n"
    "                    _res_txt = _rf.read().strip()\n"
    "                if _res_txt:\n"
    "                    window_out('【最终结果】')\n"
    "                    window_out(_res_txt[:2000])\n"
    "        except Exception:\n"
    "            pass\n"
    "        window_out('【执行完毕】')\n"
    "    except Exception as e:\n"
    "        window_out('【执行失败】' + str(e))\n"
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
    "        _root.title('程序3 · 运行窗口（自动运行；需要输入时请在下框填写）')\n"
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
    "            _param_frm = _tk.LabelFrame(_root, text='参数输入（标签由 AI 程序定义；填写后点「提交参数」，给程序自己用）')\n"
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
    "                _out_text.insert('end', '【已提交参数给程序】\\n')\n"
    "                _out_text.see('end')\n"
    "            _tk.Button(_param_frm, text='提交参数', command=_submit_params).pack(pady=2)\n"
    "        # 通用输入区：window_in 用（AI 问一般问题时输入）\n"
    "        _in_row = _tk.Frame(_root)\n"
    "        _in_row.pack(fill='x', padx=8, pady=4)\n"
    "        _tk.Label(_in_row, text='输入：').pack(side='left')\n"
    "        _in_var = _tk.StringVar()\n"
    "        _in_entry = _tk.Entry(_in_row, textvariable=_in_var, font=('Consolas', 11))\n"
    "        _in_entry.pack(side='left', fill='x', expand=True, padx=4)\n"
    "        def _submit():\n"
    "            _io['in_val'][0] = _in_var.get().strip()\n"
    "            _io['in_evt'].set()\n"
    "            _in_var.set('')\n"
    "        _in_entry.bind('<Return>', lambda e: _submit())\n"
    "        _tk.Button(_in_row, text='提交', command=_submit).pack(side='left')\n"
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
    "        print('【程序3 已结束】')\n"
    "    except Exception as e:\n"
    "        _io['gui'] = None\n"
    "        print('窗口启动失败（无图形环境），改为命令行模式运行: ' + str(e))\n"
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
    root.title("主程序（程序2）· LLM 连接 + 识别生成程序3 · v12")
    root.resizable(True, True)   # 允许自由缩放：拖动窗口边缘可等比调整，日志区自动占满剩余空间
    _sw = root.winfo_screenwidth()
    _sh = root.winfo_screenheight()
    _w = min(840, max(680, (_sw or 9999) - 160))
    _h = min(560, max(440, (_sh or 9999) - 200))   # 唤醒即小窗口；布局紧凑，底部运行栏默认可见；可再拖大/最大化
    root.geometry("%dx%d+%d+%d" % (_w, _h, max(0, (_sw - _w) // 2), max(0, (_sh - _h) // 2)))
    root.minsize(640, 420)
    apply_modern_style(root)

    ttk.Label(root, text="任务: " + TASK, font=("", 12, "bold")).grid(
        row=0, column=0, sticky="w", padx=14, pady=(12, 4))

    cfg_frame = ttk.LabelFrame(root, text="LLM 配置（OpenAI 兼容接口）")
    cfg_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 0))
    cfg_frame.columnconfigure(1, weight=2)
    cfg_frame.columnconfigure(3, weight=1)

    preset_var = tk.StringVar(value="火山方舟/豆包")
    note_var = tk.StringVar(value=PLATFORM_PRESETS["火山方舟/豆包"]["note"])

    v_base = tk.StringVar(value=cfg["base_url"])
    v_key = tk.StringVar(value="")
    v_model = tk.StringVar(value=cfg["model"])
    v_timeout = tk.StringVar(value=str(cfg["timeout"]))
    v_run_timeout = tk.StringVar(value=str(cfg["run_timeout"]))
    v_web = tk.BooleanVar(value=bool(cfg.get("web_search")))

    preset_combo = ttk.Combobox(cfg_frame, textvariable=preset_var,
                                values=list(PLATFORM_PRESETS.keys()), state="readonly")
    ttk.Label(cfg_frame, text="平台预设:").grid(row=0, column=0, sticky="w", padx=8, pady=2)
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

    ttk.Label(cfg_frame, text="接口地址 base_url:").grid(row=1, column=0, sticky="w", padx=8, pady=2)
    v_base_combo = ttk.Combobox(cfg_frame, textvariable=v_base)
    v_base_combo.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=2)
    ttk.Label(cfg_frame, text="API Key:").grid(row=2, column=0, sticky="w", padx=8, pady=2)
    ttk.Entry(cfg_frame, textvariable=v_key).grid(row=2, column=1, columnspan=2, sticky="ew", padx=8, pady=2)
    ttk.Label(cfg_frame, text="模型 model:").grid(row=3, column=0, sticky="w", padx=8, pady=2)
    model_combo = ttk.Combobox(cfg_frame, textvariable=v_model, values=COMMON_MODELS)
    model_combo.grid(row=3, column=1, sticky="ew", padx=8, pady=2)
    ttk.Label(cfg_frame, text="调用超时(秒):").grid(row=3, column=2, sticky="w", padx=8, pady=2)
    v_timeout_entry = ttk.Entry(cfg_frame, textvariable=v_timeout, width=8)
    v_timeout_entry.grid(row=3, column=3, sticky="ew", padx=8, pady=2)
    on_preset()
    ttk.Label(cfg_frame, text="运行超时(秒):").grid(row=4, column=0, sticky="w", padx=8, pady=2)
    v_run_entry = ttk.Entry(cfg_frame, textvariable=v_run_timeout, width=8)
    v_run_entry.grid(row=4, column=1, sticky="w", padx=8, pady=2)
    ttk.Label(cfg_frame, textvariable=note_var, foreground="#8a5a00", wraplength=600,
              justify="left").grid(row=4, column=2, columnspan=2, sticky="w", padx=8, pady=(2, 6))
    ttk.Label(cfg_frame, text="信息输入方式:").grid(row=5, column=0, sticky="w", padx=8, pady=2)
    input_mode_var = tk.StringVar(value=cfg.get("input_mode", "window3"))
    input_mode_combo = ttk.Combobox(cfg_frame, textvariable=input_mode_var, state="readonly",
                                     values=["window3", "window2"])
    input_mode_combo.grid(row=5, column=1, sticky="w", padx=8, pady=2)
    ttk.Checkbutton(cfg_frame, text="联网搜索（需模型支持：Doubao 系列/DeepSeek R1）",
                    variable=v_web).grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 2))
    ttk.Label(cfg_frame, foreground="#5a5a5a", wraplength=640, justify="left",
              text="window3=输入在程序3 窗口（需要输入时跳过窗口2 采集，直接生成并运行程序3）；"
                   "window2=输入在程序2 窗口（运行窗口2 采集，采集阶段暂停等您在「信息输入」框填写）"              ).grid(row=5, column=2, columnspan=2, sticky="w", padx=8, pady=(2, 6))

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
    collect_mode_var.set("采集方式：LLM 收集（完全靠 LLM 采集信息；点「采集 LLM 信息并运算」）")

    llm_run_btn = ttk.Button(btns, text="采集 LLM 信息并运算")
    llm_chat_btn = ttk.Button(btns, text="向 LLM 输入信息")
    stop_btn = ttk.Button(btns, text="停止")

    # ---- 信息输入区（单框：需要提交给 LLM 的信息都填在这里）----
    env_frame = ttk.LabelFrame(root, text="信息输入（要提交给 AI 的信息都填在这里，如账号/密码/授权码等凭据，一行一项）")
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
    input_frame = ttk.LabelFrame(root, text="LLM 输出（AI 回复全部显示在此）")
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
    text_box.insert("1.0", "（LLM 的回复将全部显示在这里）")
    text_box.config(state="disabled")
    ttk.Label(input_frame,
              text="提示：本框为只读输出区，显示 LLM 返回的全部信息；需要向 LLM 提交信息时，"
                   "请在「信息输入」框填写后点击「向 LLM 输入信息」按钮。",
              foreground="#5a5a5a", wraplength=740, justify="left").grid(
        row=1, column=0, sticky="w", padx=8, pady=(0, 6))
    input_frame.columnconfigure(0, weight=1)

    def ask_user(question):
        """暂停等待用户输入：显示问题；等待用户在「信息输入」框填写信息后
        点击「向 LLM 输入信息」按钮提交给 LLM，提交后流程继续。期间可被「停止」按钮打断。"""
        log_queue.put(f"[需要用户输入] {question}")
        log_queue.put("[需要用户输入] 请在「信息输入」框填写所需信息（服务器/账号/授权码等，一行一项），"
                      "然后点击「向 LLM 输入信息」按钮提交给 LLM ...")
        user_input_event.clear()
        user_input_answer["value"] = None
        waiting_input["flag"] = True
        # 「向 LLM 输入信息」按钮始终可用：等待补充信息期间直接点击提交
        while user_input_answer["value"] is None and not STOP_REQUESTED[0]:
            user_input_event.wait(timeout=1)
        waiting_input["flag"] = False
        if STOP_REQUESTED[0]:
            log_queue.put("[停止] 等待用户输入被「停止」按钮打断")
            return ""
        answer = user_input_answer["value"] or ""
        if answer:
            # 提交后把最新环境信息落盘（采集/程序3 从中读取）
            save_env_info(env_box.get("1.0", "end").strip())
        log_queue.put(f"[需要用户输入] 已提交: {summary(answer, 100)}")
        return answer

    def emit(msg):
        log_queue.put(str(msg))

    def _prepare_cfg():
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("提示", f"配置数字格式错误: {e}")
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
        """统一入口：校验配置 → 锁定按钮 → 后台线程执行。"""
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
                emit(f"[主程序] 失败: {e}")
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=run, daemon=True).start()
        return True

    def on_llm_run():
        """采集 LLM 信息并运算：强制 LLM 收集 → 格式化 → 生成程序3 → 运行"""
        _start_work("执行中（LLM 收集 → 格式化 → 生成程序3 → 运行）...",
                    lambda c: run_auto(c, emit=emit, popup=True, ask_user=ask_user, force_mode="llm"))


    def on_auto():
        """自动执行：LLM 采集信息（必要时复用已转换结果）→ 生成程序3 → 运行"""
        _start_work("自动执行中（LLM 采集 → 生成程序3 → 运行）...",
                    lambda c: run_auto(c, emit=emit, popup=True, ask_user=ask_user, force_mode=None))

    def on_llm_chat():
        """向 LLM 输入信息：提交「信息输入」框内容给 LLM，回复显示在 LLM 输出区。
        任何时候都能提交（包括程序运行中）；若正等待用户补充信息（AI 判断需要），
        提交后解除等待、主流程继续。"""
        env_text = env_box.get("1.0", "end").strip()
        if not env_text:
            messagebox.showwarning("提示", "请在「信息输入」框填写要提交给 LLM 的信息")
            return
        new_cfg = _prepare_cfg()
        if new_cfg is None:
            return
        was_running = state["running"]
        if not was_running:
            state["running"] = True
            llm_run_btn.config(state="disabled")
            stop_btn.config(state="normal")
        status_var.set("发送给 LLM 中 ...")
        content = "用户提供的信息（请据此继续）：\n" + env_text
        emit(f"[调试] 发送给 LLM: {summary(content, 200)}")

        def work():
            _was_waiting = waiting_input["flag"]
            try:
                reply = llm_ask([{"role": "user", "content": content}], new_cfg)
                # 跨线程不能直接操作 Tk 控件：回复经队列交给主线程 poll() 显示到 LLM 输出区
                log_queue.put(("_chat_reply", reply))
                if _was_waiting:
                    # 等待补充信息场景：提交给 LLM 后解除等待，主流程继续（不解除运行状态）
                    waiting_input["flag"] = False
                    set_user_answer("环境信息已提交给 LLM（AI 回复见输出区），主流程继续")
                elif not was_running:
                    log_queue.put(("_done", None))
                # 运行中提交：只显示回复，不打扰主流程
            except Exception as e:
                emit(f"[调试] 发送失败: {e}")
                if _was_waiting:
                    emit("[环境信息] 提交失败：请检查 LLM 配置后重试（流程仍在等待提交）")
                elif not was_running:
                    log_queue.put(("_done", None))
            finally:
                _flush_dialog_log()  # 人工调试的发送+回复也写入 LLM对话记录.txt

        threading.Thread(target=work, daemon=True).start()

    def on_stop():
        """停止按钮：请求停止当前流程，并终止正在运行的子进程（收集脚本 / 程序3）"""
        STOP_REQUESTED[0] = True
        proc = CURRENT_PROC[0]
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception as _e:
                pass
            emit("[停止] 已终止当前运行的子进程，流程将在下一步退出")
        else:
            emit("[停止] 已请求停止（当前无运行中的子进程，等待流程退出）")

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

    status_var = tk.StringVar(value="就绪")
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
                    text_box.insert("end", "\n\n===== LLM 回复 =====\n" + reply)
                    text_box.see("end")
                    text_box.config(state="disabled")
                    log_list.insert("end", "[调试] LLM 回复已显示在 LLM 输出区")
                    continue
                if isinstance(item, tuple) and item[0] == "_done":
                    state["running"] = False
                    llm_run_btn.config(state="normal")
                    llm_chat_btn.config(state="normal")
                    stop_btn.config(state="disabled")
                    status_var.set("执行完毕")
                    continue
                if isinstance(item, tuple) and item[0] == "_feedback":
                    # 全自动：文字反馈直接记日志；脚本反馈后台线程执行，不弹窗等待点击
                    kind, payload = item[1], item[2]
                    if kind == "text":
                        log_list.insert("end", "[反馈·文字] " + str(payload))
                    else:
                        def _exec_fb(code=payload):
                            try:
                                exec(compile(code, "<feedback_script>", "exec"),
                                     {"__name__": "__main__"})
                                log_list.insert("end", "[反馈·脚本] 执行完成")
                            except Exception as e:
                                log_list.insert("end", f"[反馈·脚本] 执行失败: {e}")
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
        log_list.insert(0, "[自动] 程序2 已打开，自动开始（按收集方案自动采集 → 识别 → 生成程序3 → 运行）")
        log_list.see(0)

    # 打开程序2 后自动工作（约 1.5 秒后启动完整流程）
    root.after(1500, auto_start)
    return root


def main():
    ap = argparse.ArgumentParser(
        description="主程序（程序2）: LLM 识别 → 结果4 → 生成并运行 程序3")
    ap.add_argument("--auto", action="store_true", help="命令行自动执行（无界面）")
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
    cur = os.path.join(BASE_DIR, "任务输出", "当前任务.txt")
    if not os.path.exists(cur):
        emit("[重建] 未找到 任务输出/当前任务.txt：请先完成一次训练")
        return None
    with open(cur, "r", encoding="utf-8-sig") as f:
        root_dir = f.read().strip()
    if not os.path.isdir(root_dir):
        emit(f"[重建] 最近任务文件夹不存在: {root_dir}")
        return None
    mem_dir = os.path.join(root_dir, "记忆")

    def read(name):
        p = os.path.join(mem_dir, name)
        if not os.path.exists(p):
            return ""
        with open(p, "r", encoding="utf-8-sig") as f:
            return f.read()

    task = read("任务描述.txt")
    r0, r1 = read("结果0.txt"), read("结果1.txt")
    plan = read("收集方案.txt")
    r2, r3, r6 = read("结果2.txt"), read("结果3.txt"), read("结果6.txt")
    if not (r1 and plan and r2 and r3):
        emit("[重建] 上次训练结果不完整（缺 结果1/收集方案/结果2/结果3），请重新训练")
        return None
    new_cfg = dict(cfg)
    if not new_cfg.get("api_key"):
        kp = os.path.join(mem_dir, "密钥.txt")
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
    main_path = os.path.join(root_dir, "主程序.py")
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
            emit("[停止] 已收到停止请求，训练中止")
            return True
        return False

    try:
        emit("=" * 64)
        emit(f"任务: {task}")
        emit(f"[训练] 本次训练将使用 LLM 配置 → {cfg.get('base_url')} | 模型: {cfg.get('model')} | "
             f"Key: {('****'+cfg['api_key'][-4:]) if cfg.get('api_key') else '（空）'}")
        # 每一步独立发送 Prompt，不累积对话历史，只粘贴必要的上下文

        # 独立步骤：程序开始之前，先向 LLM 单独发送「开启新对话」prompt
        emit("[开启新对话] 程序开始之前，先向 LLM 发送「开启新对话」prompt ...")
        r_new_chat_1 = ask_llm(cfg, [{"role": "user", "content": "开启新对话"}], mock, "new_chat")
        emit(f"  AI 响应: {summary(r_new_chat_1, 200)}")
        if check_stop():
            return None

        emit("[训练 1/6] 命令0: 规划如何用 state/choice 程序解决该任务...")
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
        emit(f"  结果0(规划): {summary(r0, 300)}")
        if check_stop():
            return None

        emit("[训练 2/6] 命令1: 把工作分割为 state/choice，输出严格 JSON 定义...")
        p1 = CMD1_TEMPLATE.format(user_task=task)
        r1 = ask_llm(cfg, [{"role": "user", "content": p1}], mock, 1)
        try:
            json.loads(r1)
            emit(f"  结果1: {summary(r1)}（严格 JSON 定义，仅含 state/choice）")
        except Exception:
            emit(f"  结果1: {summary(r1)}")
            emit("  [警告] 结果1 不是严格 JSON（应仅含 states/choices 定义），建议重试训练")
        if check_stop():
            return None

        emit("[训练 3/6] 命令2: 设计信息收集方案（脚本 或 Prompt 收集，按 state 逐个决策）...")
        # 粘贴结果1 作为上下文 + 可用工具清单（脚本采集可用 batch_util 批量读取）
        prompt_collect = CMD_COLLECT_PLAN + "\n\n结果1（state 定义）：\n" + r1
        r_collect_plan = ask_llm(cfg, [{"role": "user", "content": prompt_collect}],
                                 mock, "collect_plan")
        emit(f"  收集方案: {summary(r_collect_plan, 400)}")
        if check_stop():
            return None

        emit("[训练 4/6] 命令3: 生成 state→choice 运算程序（根据 RESULT4 state 取值运算）...")
        # 只粘贴结果1 作为上下文，不粘贴收集方案（运算逻辑不依赖采集方式）+ 批量任务约束
        prompt_cmd2 = CMD2 + "\n\n" + BATCH_GUIDANCE + "\n\n结果1（state/choice 定义）：\n" + r1
        r2 = ask_llm(cfg, [{"role": "user", "content": prompt_cmd2}], mock, 2)
        emit(f"  结果2(运算程序): {summary(r2)}")
        if check_stop():
            return None

        emit("[训练 5/6] 命令4: 编写 choice 执行程序 + 窗口化输出代码（合并成完整 Python 程序）...")
        # 只粘贴结果1 作为上下文，不粘贴结果2（执行程序只依赖 choice 定义与决策输出格式）+ 批量任务约束
        prompt_cmd3 = CMD3_EXEC + "\n\n" + P3_FRAMEWORK_HINT + "\n\n" + BATCH_GUIDANCE + "\n\n结果1（state/choice 定义）：\n" + r1
        r3 = ask_llm(cfg, [{"role": "user", "content": prompt_cmd3}], mock, 3)
        emit(f"  结果3(执行程序+窗口代码): {summary(r3)}")
        if check_stop():
            return None

        emit("[训练 6/6] 命令5: 设计核验标准（流程+输出结果，不实际核验）...")
        # 粘贴结果1 作为上下文
        prompt_cmd5 = CMD5 + "\n\n结果1（state/choice 定义）：\n" + r1
        r6 = ask_llm(cfg, [{"role": "user", "content": prompt_cmd5}], mock, 6)
        emit(f"  结果6(核验标准): {summary(r6, 300)}")
        if check_stop():
            return None

        # ---- 程序代码校验与自动修复（静态：工具白名单 + 语法；直到跑通或达上限）----
        max_fix = int(cfg.get("max_fix_rounds", 5))
        for fix_round in range(1, max_fix + 1):
            problems = []
            for _label, _code in (("结果2(运算程序)", r2), ("结果3(执行程序)", r3)):
                _ok, _errs = validate_code_imports(strip_code_fence(_code), _label)
                problems.extend(_errs)
            if not problems:
                break
            emit(f"[自动修复 {fix_round}/{max_fix}] 检测到代码问题 {len(problems)} 项，正在让 LLM 重写 ...")
            for _p in problems:
                emit(f"  - {_p}")
            fix_prompt = (
                "你之前为任务生成的程序代码存在以下问题（违反工具白名单或存在语法错误）：\n"
                + "\n".join("- " + _p for _p in problems) + "\n\n"
                "硬性要求：\n"
                "1. 只使用 Python 标准库与内置 batch_util 工具，禁止 import 任何第三方库\n"
                "2. 重新输出两份完整、可运行的 Python 代码：先输出【结果2：state→choice 运算程序】代码块，"
                "再输出【结果3：choice 执行程序】代码块，均用 ```python 围栏\n"
                "任务定义（结果1）：\n" + r1
            )
            combined = ask_llm(cfg, [{"role": "user", "content": fix_prompt}], mock, "fix_code")
            _fences = re.findall(r"```(?:python)?\s*(.*?)```", combined, re.S)
            if len(_fences) >= 2:
                r2, r3 = _fences[0], _fences[1]
                emit(f"[自动修复] LLM 已重写：结果2({len(r2)}字符) + 结果3({len(r3)}字符)")
            else:
                emit("[自动修复] 未能从回复中解析出两份代码块，本轮修复无效，进入下一轮")
            if check_stop():
                return None
        if not problems:
            emit("[校验] 程序代码通过校验（工具白名单 + 语法检查）")
        else:
            emit("[校验] 已达最大修复轮次仍未通过静态校验，继续生成主程序（运行时会进一步验证）")

        # 新建文件夹 + 子文件夹 + 记忆
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_root = os.path.join(BASE_DIR, "任务输出")
        root_dir = os.path.join(out_root, f"任务_{stamp}_{safe_task_name(task)}")
        sub_dir = os.path.join(root_dir, "子文件夹")
        mem_dir = os.path.join(root_dir, "记忆")
        os.makedirs(sub_dir, exist_ok=True)
        os.makedirs(mem_dir, exist_ok=True)
        CURRENT_MEM_DIR[0] = mem_dir  # 记录钩子：此后每次 LLM 调用自动写盘对话记录
        with open(os.path.join(out_root, "当前任务.txt"), "w", encoding="utf-8-sig") as f:
            f.write(root_dir)

        save_text(os.path.join(mem_dir, "任务描述.txt"), task)
        save_text(os.path.join(mem_dir, "结果0.txt"), r0)
        save_text(os.path.join(mem_dir, "结果1.txt"), r1)
        save_text(os.path.join(mem_dir, "收集方案.txt"), r_collect_plan)
        save_text(os.path.join(mem_dir, "结果2.txt"), r2)
        save_text(os.path.join(mem_dir, "结果3.txt"), r3)
        save_text(os.path.join(mem_dir, "结果6.txt"), r6)
        # 把本次训练的 Key 写入新任务密钥文件：程序2 打开后自动流程可直接使用，
        # 避免残留旧的 密钥.txt 导致"测试新 Key 成功但自动流程仍连不上"
        if cfg.get("api_key"):
            try:
                with open(os.path.join(mem_dir, "密钥.txt"), "w", encoding="utf-8") as f:
                    f.write(cfg["api_key"])
            except Exception:
                pass
        emit(f"[训练完成] 新建文件夹: {root_dir}")
        emit(f"           子文件夹: {sub_dir}")
        emit(f"           结果0-6 与 收集方案 已保存到 记忆/（结果4=窗口化输出代码）")

        # （已按要求移除：打开程序2 之前不再发送「开启新对话」，只有任务开始时发一次）

        # 生成 程序2（主程序.py）并运行验证（动态验证：失败自动让 LLM 修复后重跑，直到跑通或达上限）
        main_path = os.path.join(root_dir, "主程序.py")
        run_ok = False
        for run_round in range(1, max_fix + 1):
            emit(f"[程序1→程序2] 生成 主程序.py（第 {run_round} 版，内嵌 结果0-6、收集方案 与配置）...")
            main_src = generate_main_program(cfg, task, r0, r1, r_collect_plan,
                                             r2, r3, r6, mock)
            save_text(main_path, main_src)
            emit(f"[程序1→程序2] 已生成: {main_path}")

            if open_ui and not mock:
                # 直接打开程序2 的可视化界面（不阻塞程序1，程序2 打开后自动工作）
                emit("[程序1→程序2] 直接打开 主程序.py 界面（程序2）...")
                subprocess.Popen([sys.executable, main_path], cwd=root_dir)
                emit("[程序2] 已打开，将自动执行：采集 → 识别 → 生成程序3 → 运行。")
                run_ok = True
                break

            # 自检/后台模式：命令行自动运行程序2 → 它生成并运行程序3
            emit(f"[程序2] 启动 主程序.py --auto（第 {run_round} 轮）...")
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
            save_text(os.path.join(mem_dir, "主程序输出.txt"), out)
            emit(f"[程序2] 退出码: {rc}")
            for line in out.strip().splitlines()[-40:]:
                emit(f"  | {line}")

            # 结果质量检查（真实训练时）：最终结果过短（只有一句文字）视为不达标，触发自动修复
            if not mock:
                _final_path = os.path.join(mem_dir, "最终结果.txt")
                _final_text = ""
                try:
                    if os.path.exists(_final_path):
                        with open(_final_path, "r", encoding="utf-8-sig") as f:
                            _final_text = f.read().strip()
                except Exception:
                    pass
                _core = re.sub(r"^文字结果[:：]\s*", "", _final_text).strip()
                if not _core:
                    emit("[验证] 结果质量不达标：未生成最终结果，触发自动修复")
                    out += "\n[结果质量不达标：未生成最终结果]"
                elif len(_core) < 15:
                    emit(f"[验证] 结果质量不达标：最终结果过短（{len(_core)} 字），触发自动修复")
                    out += f"\n[结果质量不达标：最终结果过短] {_final_text}"

            # 判定是否跑通：无失败标志，且出现了流程完成标志
            fail_markers = ["[错误]", "程序3 无输出", "未解析为 JSON", "运行超时", "Traceback",
                            "无法连接", "校验不通过", "不启动程序三", "程序3 运行超时",
                            "结果质量不达标"]
            done_markers = ("[执行完毕]", "[最终结果]", "[决策]", "[完成]")
            if out and not any(_mk in out for _mk in fail_markers) and \
                    any(_mk in out for _mk in done_markers):
                run_ok = True
                emit(f"[验证] 第 {run_round} 轮运行跑通，停止迭代")
                break

            if run_round >= max_fix:
                emit("[验证] 已达最大运行修复轮次，保留最后一版（可人工用「向 LLM 输入信息」继续修正）")
                break

            # 未跑通：收集运行输出与现场，让 LLM 重写 结果3（执行程序）
            emit(f"[自动修复 {run_round}/{max_fix}] 主程序运行未跑通，收集现场让 LLM 重写结果3 ...")
            fix_prompt = (
                "你生成的主程序运行未跑通，以下是运行输出（末尾部分）：\n"
                + (out[-3000:] if out else "（无输出）") + "\n\n"
                "任务定义（结果1）：\n" + r1 + "\n\n"
                "请只重写【结果3：choice 执行程序】的完整 Python 代码（用 ```python 围栏）：\n"
                "1. 只使用 Python 标准库与内置 batch_util，禁止第三方库\n"
                "2. 全自动运行：禁止 input()、弹窗、mainloop、sleep 等待用户；"
                "参数（登录凭据/数量/筛选条件等）：代码顶层声明 REQUIRED_INPUTS = [\"标签\",...]（最多5个），"
                "用内置函数 window_inputs() 获取 {\"标签\": 值} 字典（给程序自己用，禁止发给 LLM）；"
                "其他用户输入用 window_in(\"提示语\")（程序3 窗口弹输入框）；"
                "输出用 print（会自动显示在程序3 窗口）\n"                "7. 需要登录/凭据/账号/服务器地址等时，从常量 ENV_INFO 读取（用户提供的信息），"
                "不要自己假设或让用户再次输入"
            )
            r3 = ask_llm(cfg, [{"role": "user", "content": fix_prompt}], mock, "fix_runtime")
            emit(f"[自动修复] LLM 已重写结果3（{len(r3)}字符），重新生成主程序并重跑 ...")
            if check_stop():
                return None

        if not run_ok and not (open_ui and not mock):
            emit("[提示] 多轮迭代仍未完全跑通，产物已保留；可后续在程序2 中反馈修正")

        emit("")
        emit("[完成] 训练与生成完成")
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
        emit(f"[失败] {e}")
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
    root.title("训练程序（程序1）· LLM 自动生成 程序2/程序3 · v12")
    root.resizable(True, True)   # 允许自由缩放
    _sw = root.winfo_screenwidth()
    _sh = root.winfo_screenheight()
    _w = min(980, max(760, (_sw or 9999) - 120))
    _h = min(680, max(520, (_sh or 9999) - 130))   # 扣除任务栏/标题栏，保证日志区可见
    root.geometry("%dx%d+%d+%d" % (_w, _h, max(0, (_sw - _w) // 2), max(0, (_sh - _h) // 2)))
    root.minsize(740, 500)
    apply_modern_style(root)

    ttk.Label(root, text="您要完成什么任务？（下方内容只是示例，请替换为您自己的任务）", font=("", 14, "bold")).grid(
        row=0, column=0, sticky="w", padx=14, pady=(14, 4))
    task_var = tk.StringVar(value="例如: 捕捉10个黄金相关网页（kitco.com/gold-price-today-usa、gold.org/goldhub/data/gold-prices、cn.investing.com/commodities/gold 等），提取5个指标作为state：黄金价格、24小时涨跌幅、美元指数、避险情绪、黄金ETF资金净流入，综合判断输出决策之一：买入全仓/卖出全仓/买入半仓/卖出半仓/不买入不卖出，把决策与依据保存到 记忆/黄金决策结果.txt。登录类凭据无需写在任务里，请在下方「环境信息」输入栏填写")
    ttk.Combobox(root, textvariable=task_var, font=("", 12)).grid(
        row=1, column=0, sticky="ew", padx=14)

    cfg_frame = ttk.LabelFrame(root, text="LLM 配置（OpenAI 兼容接口）")
    cfg_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 0))

    preset_var = tk.StringVar(value="火山方舟/豆包")
    note_var = tk.StringVar(value=PLATFORM_PRESETS["火山方舟/豆包"]["note"])

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
    ttk.Label(cfg_frame, text="平台预设:").grid(row=0, column=0, sticky="w", padx=8, pady=2)
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

    cfg_row(1, "接口地址 base_url:", v_base)
    cfg_row(2, "API Key:", v_key)
    ttk.Label(cfg_frame, text="模型 model:").grid(row=3, column=0, sticky="w", padx=8, pady=2)
    model_combo = ttk.Combobox(cfg_frame, textvariable=v_model, values=COMMON_MODELS)
    model_combo.grid(row=3, column=1, sticky="ew", padx=8, pady=2)
    on_preset()
    cfg_row(4, "调用超时(秒):", v_timeout)
    cfg_row(5, "运行超时(秒):", v_run_timeout)
    ttk.Label(cfg_frame, textvariable=note_var, foreground="#8a5a00", wraplength=740,
              justify="left").grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6))
    ttk.Label(cfg_frame, text="API Key 仅本次运行有效，不落盘；下次需重新粘贴（或设环境变量 ARK_API_KEY）。",
              foreground="#5a5a5a", wraplength=740, justify="left").grid(
        row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
    ttk.Checkbutton(cfg_frame, text="联网搜索（需模型支持：Doubao 系列/DeepSeek R1）",
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

    start_btn = ttk.Button(btns, text="开始执行（生成程序2→程序3 并运行）")
    stop_btn = ttk.Button(btns, text="停止")
    test_btn = ttk.Button(btns, text="测试连接 LLM")
    rebuild_btn = ttk.Button(btns, text="用上次结果重建程序2（0 token）")

    def emit(msg):
        log_queue.put(str(msg))

    def on_test():
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("提示", f"配置数字格式错误: {e}")
            return
        v_model.set(new_cfg["model"])  # 界面显示规范化后的模型名，所见即所用
        state["running"] = True
        start_btn.config(state="disabled")
        test_btn.config(state="disabled")
        status_var.set("连接测试中 ...")

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
            messagebox.showwarning("提示", "请先输入要完成的任务")
            return
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("提示", f"配置数字格式错误: {e}")
            return
        save_config(new_cfg)
        state["running"] = True
        start_btn.config(state="disabled")
        log_list.delete(0, "end")
        status_var.set("执行中（训练 → 生成程序2 → 生成程序3 → 运行）...")

        def work():
            try:
                run_training(task, new_cfg, emit=emit, open_ui=True)
            except Exception:
                pass
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_rebuild():
        """用最近一次训练结果重建 程序2（不调用 LLM，0 token）：模板更新后无需重新训练。"""
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("提示", f"配置数字格式错误: {e}")
            return
        state["running"] = True
        start_btn.config(state="disabled")
        test_btn.config(state="disabled")
        rebuild_btn.config(state="disabled")
        status_var.set("重建程序2 中（不调用 LLM，0 token）...")

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
        log_queue.put("[停止] 已请求停止：训练将在当前 LLM 调用返回后中止（LLM 调用中无法强制中断，最多等待超时）")
        status_var.set("已请求停止 ...")

    def on_feedback():
        question = fb_var.get().strip()
        if not question:
            messagebox.showwarning("提示", "请先输入反馈问题")
            return
        if state["running"]:
            return
        try:
            new_cfg = read_cfg_from_gui()
        except ValueError as e:
            messagebox.showwarning("提示", f"配置数字格式错误: {e}")
            return
        state["running"] = True
        start_btn.config(state="disabled")
        test_btn.config(state="disabled")
        fb_btn.config(state="disabled")
        status_var.set("反馈修正中（发送 程序3 执行结果+问题 给 LLM）...")

        def work():
            try:
                feedback_fix(new_cfg, question, emit=emit, mock=False)
            except Exception as e:
                emit(f"[反馈修正] 失败: {e}")
            finally:
                log_queue.put(("_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_open_dir():
        out_dir = os.path.join(BASE_DIR, "任务输出")
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
    ttk.Button(btns, text="打开任务输出目录", command=on_open_dir).grid(row=0, column=4, padx=4)

    fb_frame = ttk.Frame(root)
    fb_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 4))
    ttk.Label(fb_frame, text="反馈问题（发送给 LLM 修正程序3）:").grid(row=0, column=0, sticky="w")
    fb_var = tk.StringVar()
    fb_entry = ttk.Combobox(fb_frame, textvariable=fb_var, font=("", 11))
    fb_entry.grid(row=0, column=1, sticky="ew", padx=8)
    fb_btn = ttk.Button(fb_frame, text="发送反馈并修正")
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

    status_var = tk.StringVar(value="就绪")
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
                    status_var.set("完成")
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
                    help="无界面自检：模拟 LLM 跑通 程序1→程序2→程序3 全流程")
    ap.add_argument("--run-task", metavar="任务描述", default=None,
                    help="命令行真实运行：训练 + 生成主程序 + 运行程序3（需配置 API Key）")
    ap.add_argument("--model", default=None, help="覆盖使用的 LLM 模型名")
    args = ap.parse_args()

    def _emit(msg):
        print(msg, flush=True)

    if args.selftest:
        cfg = load_config()
        print("开始自检（模拟 LLM，不调用真实接口）...")
        run_training("自检任务: 捕捉10个网页的黄金相关5个指标（5个state），输出决策之一（买入全仓/卖出全仓/买入半仓/卖出半仓/不买入不卖出）", cfg, emit=_emit, mock=True)
        return

    if args.run_task:
        cfg = load_config()
        if args.model:
            cfg["model"] = args.model
        if not cfg.get("api_key"):
            print("错误：未配置 LLM API Key（环境变量 ARK_API_KEY 或配置文件）", flush=True)
            import sys as _sys
            _sys.exit(2)
        print("开始命令行运行任务（真实 LLM）：%s" % args.run_task, flush=True)
        run_training(args.run_task, cfg, emit=_emit)
        return

    root = build_gui()
    root.mainloop()


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
