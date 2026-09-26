"""A-side 受限住房网页搜索工具测试。"""

import unittest

import httpx

from property_agent.requirements.housing_questions import DuckDuckGoHousingWebSearch


class HousingWebSearchTests(unittest.TestCase):
    """验证搜索用途限制、结果解析和跳转链接还原。"""

    def test_duckduckgo_lite_results_are_parsed(self) -> None:
        """搜索工具应返回原始来源 URL，而不是 DuckDuckGo 跳转地址。"""

        page = """
        <html><body><table>
          <tr><td><a rel="nofollow"
            href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Frent&amp;rut=x"
            class="result-link">Singapore Rent Report</a></td></tr>
          <tr><td class="result-snippet">Median rent by property type.</td></tr>
        </table></body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            """返回固定 DuckDuckGo Lite HTML。"""

            self.assertIn("Singapore housing information", request.url.params["q"])
            return httpx.Response(200, request=request, text=page)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            tool = DuckDuckGoHousingWebSearch(client=client)
            results = tool.search(
                "What is the average rental in Singapore?",
                purpose="answer_housing_question",
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Singapore Rent Report")
        self.assertEqual(results[0].url, "https://example.com/rent")
        self.assertEqual(results[0].snippet, "Median rent by property type.")

    def test_search_rejects_unsupported_purpose(self) -> None:
        """A 的网页搜索不能被复用于房源检索或推荐。"""

        tool = DuckDuckGoHousingWebSearch()
        with self.assertRaisesRegex(ValueError, "unsupported purpose"):
            tool.search(
                "Find current listings",
                purpose="listing_recommendation",  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
