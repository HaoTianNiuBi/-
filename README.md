# 栖语 v0.6

栖语是一款提供可质证长期记忆的 AI 陪伴 Web 应用。用户可以在同一个记忆库跨会话继续聊，从回复跳回依据原话，也能纠正记录、选择适用范围或清理数据。

React、TypeScript 和 GSAP 负责对话及档案交互。FastAPI 根据当前空间和记忆库筛选 SQLite 记录，调用 DeepSeek 生成回复与候选记忆，再校验原文、权限和版本。

## 参赛团队

智维空间，绵阳城市学院。参赛组别为传智杯 AI WEB 网页开发挑战赛省赛 B 组，报名作品名为栖语。

| 角色 | 分工 |
| --- | --- |
| 队长 | 后端、数据与部署 |
| 队员 | 产品与 AI 融合 |
| 队员 | 小程序前端与体验 |

## 本地运行

验证环境为 Windows、Python 3.14、Node.js 24。从仓库根目录安装锁定依赖。

```powershell
py -3.14 -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
npm --prefix frontend ci
```

按 [.env.example](.env.example) 的变量名在当前 PowerShell 会话配置自己的 DeepSeek Key，再启动本地 Demo。设置密钥的命令会遮蔽输入。

```powershell
$qiyuKey = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $qiyuKey).Password
$env:QIYU_KEY_MODE = 'environment'
node frontend/scripts/start-demo.cjs
```

打开 http://127.0.0.1:5173/。后端监听 127.0.0.1:8765。未配置模型时可启动界面和运行自动测试。真实回复需有可用的 DeepSeek Key。

## 演示步骤

1. 在生活库输入「我喜欢桂花乌龙茶。」在记忆台账查看入账和来源原话。
2. 新建生活库对话，询问「你记得我偏好哪种茶吗？」并打开回复引用。切换到学习库，在新对话重复提问。
3. 打开数据管理，按生活库[导出 JSON 或 Markdown](docs/verification/v0.6-data.jpg)。
4. 回台账[删除茶偏好](docs/verification/v0.6-delete.jpg)，在生活库新对话复查。档案中还可以查看同库时间线、跟踪和认知变化。

## 构建与测试

在新的 PowerShell 会话中执行。后端自动测试使用临时数据和测试替身。

```powershell
npm --prefix frontend run build
Push-Location backend
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location
```

本地验证通过 93 项后端测试、前端生产构建及同源运行。另以 [8 个虚构用例](docs/verification/v0.6-model-eval.json)使用真实 DeepSeek 模型检查入账、来源、分库与删除。真实模型样本与自动测试分别记录。

Dockerfile 和 Compose 提供同源部署配置，服务端口默认绑定本机回环地址。容器由环境读取模型 Key。此版本的运行与验证结果均来自本地。
