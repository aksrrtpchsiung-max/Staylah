"""A-side restricted housing web search tool tests."""

import unittest

import httpx

from property_agent.requirements.housing_questions import DuckDuckGoHousingWebSearch


class HousingWebSearchTests(unittest.TestCase):
    """Verify search purpose restrictions, result parsing, and redirect link restoration."""

    def test_duckduckgo_lite_results_are_parsed(self) -> None:
        """The search tool should return the original source URL, not the DuckDuckGo redirect address."""

        page = """
        <html><body><table>
          <tr><td><a rel="nofollow"
            href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Frent&amp;rut=x"
            class="result-link">Singapore Rent Report</a></td></tr>
          <tr><td class="result-snippet">Median rent by property type.</td></tr>
        </table></body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            """Return fixed DuckDuckGo Lite HTML."""

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
        """A's web search cannot be reused for property listing retrieval or recommendations."""

        tool = DuckDuckGoHousingWebSearch()
        with self.assertRaisesRegex(ValueError, "unsupported purpose"):
            tool.search(
                "Find current listings",
                purpose="listing_recommendation",  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
