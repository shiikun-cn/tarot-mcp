# Tarot MCP Service (Python) - 最小可用模板

## 简介
这是一个最小可用的 Python 服务，提供 `/draw_one` 和 `/draw_three` 两个 HTTP 接口，用于返回随机塔罗牌（并在 session 内去重）。设计用于与小智AI（通过 MCP）或 imcp.pro 等平台对接。

- 支持 Redis（可选）用于会话持久化；若未配置 REDIS_URL 则使用内存（容器重启会丢失会话）。
- 推荐将 `data/tarot.csv` 替换为你完整的 78 张 CSV（包含 `Index` 列）。

## 项目结构
