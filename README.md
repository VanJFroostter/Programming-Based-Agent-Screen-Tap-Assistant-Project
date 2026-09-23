# Programming-Based-Agent-Screen-Tap-Assistant-Project Agent基于编程的屏幕点击辅助程序器
Current decision-making operations of Agents such as screen tapping, swiping and text input consume massive volumes of tokens. Jev put forward a solution: abstract these trivial tasks into a State-Choice paradigm using JSON syntax, then train an AI to resolve State-Choice problems.

Nevertheless, if such tasks can be abstracted under the State-Choice framework, generating dedicated programs via AI to handle these simple tasks will deliver far higher efficiency with a zero hallucination rate. Accordingly, the author launched this project to build a compiler that assists AI in training lightweight programs optimized for repetitive basic interface operations.

## Minimal Working Paradigm (Screen Swipe Example)

For screen swiping tasks, the system first captures screen content and feeds it to an LLM, which outputs structured JSON state data (two states: Confirm Button Visible / No Confirm Button). A backend program then parses the JSON output: it halts swiping if a confirm button is detected, and continues scrolling if no confirm button exists (see attached diagram).

![Agent Workflow Diagram](./exported_image.png)

## Ultimate Project Goal

Enable AI to design hybrid LLM+code workflows to offload basic decision-making tasks—including screen tapping/swiping, email categorization, and simulated input for the Super Mario game—so as to drastically accelerate AI processing of bulk low-complexity tasks.

## Core Methodology

1. AI designs workflows based on the State-Choice state decision framework, solidifying core decision logic into executable code.
2. Minimize LLM invocations during runtime: all conditional judgments are handled by local code, while the LLM is only tasked with semantic comprehension and visual parsing.

In essence, the workflow works like a precompilation pipeline: developers predefine JSON schemas, State-Choice logic and window interaction structures, then let AI complete and refine the codebase to process large batches of simple tasks reliably via the predefined architecture.

## Three-Tier Architecture

1. Trainer Program: Invokes an AI to generate a primary executable script tailored to a target task.
2. Primary Executable Script: Two operating modes:
   - Local script mode: Collects JSON data locally and executes logical computations;
   - LLM-aided mode: Retrieves raw data via LLM and converts outputs into standardized JSON for downstream computation.
3. Feedback Loop via Trainer Program: Captures runtime execution results and feeds them back to the AI, which iteratively revises the source code to deliver smoother, more stable interactions.

## Current Testing Scope

The system has not yet been integrated into Agent screen-tap test pipelines. Validated test cases so far include email classification and web gold price data scraping with automated decision output. The detailed execution pipeline is outlined as follows:

![Agent Workflow Diagram](./exported_image4.png)


现在的agent屏幕点击、滑动、输入等决策需要消耗大量的token。Jev提出一种思路，将这些简单任务用json语言抽象为state-choice的模式，并训练ai解决state-choice问题。然而，如果能够抽象为state-choice模式的话，那么，用AI编写程序来解决这些简单问题会更加高效，而且0幻觉率。因此，作者做这个项目的目的是为了制作一个编译器，它辅助AI训练出一个可以高效进行这些简单任务的程序。

一个最为简单的模式是：针对屏幕滑动任务，识别屏幕交给LLM输出json格式的state（有确认按钮/无确认按钮），之后程序读取，若有确认按钮则停止滑动，若无确认按钮则继续滑动。如下图。

![Agent Workflow Diagram](./exported_image2.png)

本程序的最终目标是让AI设计LLM+程序的工作流，辅助简单决策，例如屏幕点击or滑动，邮件分类，马里奥游戏模拟点击，以提升AI处理大批量简单任务的速度。

方法是，AI 负责设计工作流（state-choice状态决策框架），把决策逻辑固化成代码；运行阶段尽量少调用大模型，用代码做判断，LLM 只负责做理解类的工作。

相当于预编译一个程序，把它的json，state和choice，还有窗口交互结构写好，然后让AI把它编写完善，完善到能够利用这套结构完成大批量简单任务。

![Agent Workflow Diagram](./exported_image3.png)

具体的架构分为三步：1 训练程序调用AI针对某个任务设计出一个主程序 2 主程序，要么是通过脚本在本地采集Json信息并运算，要么是通过LLM采集信息，并转换为Json格式进行运算。 3 训练程序可以拾取结果进行反馈，让AI修改代码，以实现更流畅的操作

目前该程序尚未接入Agent试验屏幕点击功能，仅试验了邮件分类功能，以及调取网页黄金价格信息输出决策的功能。具体流程如下：



