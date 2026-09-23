This branch enables the LLM to crawl information from web pages, convert the retrieved data into state-choice format and feed it into the program. The training program then guides the LLM to compile a program built on the state-choice architecture, which will be used for decision-making.

Verified working example: A decision task that crawls gold-related news from 10 web pages and outputs buy/sell trading decisions.

Model & plugin used: DeepSeek-v4-1-flash-260910 on Volcano Ark platform with internet search plugin enabled.



### Token Consumption Note

- The full training process consumes around 100,000 tokens.
  
- After training, standalone execution of the optimized main program consumes fewer than 500 tokens only.
  
This architecture drastically cuts down total token overhead for completing targeted tasks.

A sliding & click decision program will be trained for agents later, with the same core logic.



**User Manual（LLM info gathering feature）**

The complete Python file (both Chinese & English bilingual editions) has been uploaded. Please follow the operation steps below for the LLM info gathering feature:

1. Please put the py file into an empty folder
2. Make sure the web search option is checked
3. Fill in your API key and LLM model type (DeepSeek V4.1 Flash has been verified to work normally)
4. Hit the LLM connection test button and wait for instant feedback; the test response is very fast
5. Click the start execution button to launch automatic running. The program will run fully autonomously until it outputs the final decision options.  (Tip: Flash LLMs are strongly recommended, otherwise the running speed will be slow)
6. If the generated result fails to meet your requirements, click the feedback button inside the training module. The system will send the current code back to LLM for revision, specifically modifying codes of the state-choice handler.
7. When the LLM prompts that extra supplementary information is required, input the relevant content in the main program (Program 2) and submit it; the LLM will resume subsequent workflows automatically.
8. Once the training process finishes, you can run tasks independently by launching the main program (Program 2) stored under the `task output` folder.
9. Input your requirements in Program 1 to train any other program to satisfy your demands



---


本Branch让LLM去网页抓取信息，转换成state-choice格式输入程序，并由训练程序引导LLM编写一个state-choice结构的程序，用该程序做出决策。

已跑通的示例：抓取10个网页的黄金相关新闻并做出购买或卖出决策的决策任务。
采用火山方舟平台deepseek-v4-1-flash-260910模型+联网插件。



### Token 消耗说明

完整训练流程约消耗 10万 个 token
   
训练完成后，单独运行主程序，仅需消耗不足 500 个 token
   
该架构可大幅降低完成指定任务所需的总 token 开销

之后将训练滑动/点击决策程序供agent使用，原理相同





# LLM信息收集类决策器操作说明书

配套完整 py 文件（中英文双语版本）已上传，请按以下步骤操作：

1. 将py文件放入一个空文件夹
2. 确保已经勾选联网搜索按钮
3. 填入 API 密钥并选择对应 LLM 模型类型（DeepSeek V4.1 Flash 已实测可正常运行）
4. 点击 LLM 连接测试按钮，等待反馈，测试响应速度极快
5. 点击开始执行按钮启动自动运行，程序全程自主运算，直至输出最终决策选项   提示：强烈推荐使用 Flash 系列大模型，否则运行速度会十分缓慢
6. 若输出结果不好，点击训练模块内的反馈按钮，系统会将现有代码回传给 LLM 重写，针对性修改 state-choice 逻辑处理代码
7. 当 LLM 提示需要补充信息时，在主程序（程序 2）内填写相关内容并提交，LLM 会接续完成后续流程
8. 训练完成后，可直接启动「任务输出」文件夹内的主程序（程序 2），独立执行该任务
9. 在程序 1 中输入您的任意需求，即可针对该需求为您训练程序，实现目标
