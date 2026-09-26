"""Convert stored recommendations into the existing HTTP card shape."""
class RecommendationCards:
    """Resolve cards only from the persisted snapshot and populate the trusted session cache."""
    async def _recommendation_cards(self, session, *, run_id, recommendation):
        if not recommendation or not run_id or self.orchestrator is None:
            return []
        snapshot = await self.orchestrator.decision_graph.aget_state(
            {'configurable': {'thread_id': run_id}}
        )
        listings = {
            item['listing_key']: item
            for item in (snapshot.values.get('listing_snapshot') or {}).get('items', [])
        }
        cards = []
        for item in recommendation.get('ordered_items', []):
            listing = listings.get(item['listing_key'], {})
            card = {
                **item,
                **{
                    key: listing.get(key)
                    for key in (
                        'title',
                        'price',
                        'bedrooms',
                        'attributes',
                        'evidence',
                        'source_url',
                        'source_mode',
                    )
                },
            }
            card['listing_key'] = item['listing_key']
            cards.append(card)
            session['cards'][item['listing_key']] = card
        return cards
