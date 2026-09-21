"""通过本地 Agent Server 对话；客户端不需要读取 DeepSeek API Key。"""

import argparse
import json

from langgraph_sdk import get_sync_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", help="发送一条消息后退出；不填则进入交互模式")
    args = parser.parse_args()
    client = get_sync_client(url="http://127.0.0.1:2024")
    thread = client.threads.create()
    seen = 0
    print("会话：", thread["thread_id"])

    def send(text):
        nonlocal seen
        result = client.runs.wait(
            thread["thread_id"],
            "agent",
            input={"messages": [{"role": "user", "content": text}]},
            config={"recursion_limit": 20},
        )
        for message in result["messages"][seen:]:
            if message["type"] == "ai":
                for call in message.get("tool_calls", []):
                    print(f"模型请求工具：{call['name']}({json.dumps(call['args'], ensure_ascii=False)})")
                if message.get("content"):
                    print("Agent：", message["content"])
            elif message["type"] == "tool":
                print(f"工具结果 [{message.get('name', '')}]：{message['content']}")
        seen = len(result["messages"])
        print(f"本轮模型调用：{result['llm_calls']} 次\n")

    if args.message:
        send(args.message)
        return
    print("输入问题开始；输入 /quit 退出。同一会话可继续追问。")
    while True:
        try:
            text = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "/quit":
            break
        if text:
            send(text)


if __name__ == "__main__":
    main()
