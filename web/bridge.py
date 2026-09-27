"""Browser sessions, existing orchestration calls and response adaptation."""
from __future__ import annotations
import asyncio
from dataclasses import asdict
import json
import re
from uuid import uuid4
from property_agent.requirements.workflow_constants import DEFAULT_USER_ID
from .cards import RecommendationCards

class WebBridge(RecommendationCards):
    """Invoke the existing orchestration serially per browser session; only accept property identifiers already returned by the server."""

    PROGRESS_LABELS = {
        ('A', 'requirement_turn'): 'Understanding your requirements…',
        ('B', 'prepare_query'): 'Preparing your search…',
        ('B', 'build_search_plan'): 'Building your search…',
        ('B', 'initial_search'): 'Initializing the search…',
        ('B', 'search_for_request'): 'Searching available homes…',
        ('C', 'retrieve'): 'Gathering listing details…',
        ('C', 'evaluate'): 'Evaluating the best matches…',
        ('C', 'review'): 'Reviewing recommendations…',
        ('C', 'decide_next'): 'Finalizing recommendations…',
        ('A', 'return_from_c'): 'Preparing your results…',
    }

    def __init__(self, orchestrator, *, turn_timeout_seconds=330):
        self.orchestrator = orchestrator
        self.turn_timeout_seconds = turn_timeout_seconds
        self.sessions = {}

    def create_session(self, conversation_id=None):
        if conversation_id is not None:
            if not isinstance(conversation_id, str) or not re.fullmatch(
                r"web-[0-9a-f]{32}", conversation_id
            ):
                raise ValueError('Invalid conversation ID.')
            chat = getattr(self.orchestrator, 'chat', None)
            if chat is not None:
                try:
                    chat.ensure_conversation(conversation_id, user_id=DEFAULT_USER_ID)
                    if hasattr(chat, 'set_conversation_title'):
                        first_user_message = next(
                            (
                                item['text']
                                for item in chat.list_messages(conversation_id, limit=100)
                                if item['role'] == 'user'
                            ),
                            None,
                        )
                        if first_user_message:
                            chat.set_conversation_title(
                                conversation_id,
                                user_id=DEFAULT_USER_ID,
                                title=first_user_message,
                            )
                except PermissionError as exc:
                    raise ValueError('Conversation unavailable.') from exc
        token = uuid4().hex
        self.sessions[token] = dict(conversation_id=conversation_id or f'web-{uuid4().hex}', cards={}, lock=asyncio.Lock(), replies={}, cancelled=set(), active=None, progress=None)
        return token


    async def session_info(self, token):
        session = self._session(token)
        chat = getattr(self.orchestrator, 'chat', None)
        history = chat.list_messages(session['conversation_id'], limit=100) if chat is not None else []
        if self.orchestrator is not None and history:
            config = {'configurable': {'thread_id': session['conversation_id']}}
            try:
                snapshot = await self.orchestrator.a_graph.aget_state(config)
                processed = (snapshot.values or {}).get('processed_turns') or {}
            except Exception:
                processed = {}
            for message in history:
                prefix = 'assistant:'
                if message['role'] != 'assistant' or not message['message_id'].startswith(prefix):
                    continue
                turn = processed.get(message['message_id'][len(prefix):])
                if not isinstance(turn, dict) or not turn.get('recommendation'):
                    continue
                render_data = {
                    'assistant_response': turn.get('assistant_response', ''),
                    'recommendation': turn['recommendation'],
                    'cards': await self._recommendation_cards(
                        session,
                        run_id=turn.get('run_id'),
                        recommendation=turn['recommendation'],
                    ),
                }
                message['render_data'] = render_data
        return {
            'session_id': token,
            'conversation_id': session['conversation_id'],
            'history': history,
            'favorites': self.list_favorites(token),
        }

    def conversations(self):
        chat = getattr(self.orchestrator, 'chat', None)
        if chat is None or not hasattr(chat, 'list_conversations'):
            return []
        conversations = chat.list_conversations(user_id=DEFAULT_USER_ID, limit=50)
        favorites = getattr(self.orchestrator, 'favorites', None)
        counts = {}
        if favorites is not None and hasattr(favorites, 'counts_by_conversation'):
            counts = favorites.counts_by_conversation(
                [item['conversation_id'] for item in conversations],
                user_id=DEFAULT_USER_ID,
            )
        elif favorites is not None:
            counts = {
                item['conversation_id']: len(
                    favorites.list(
                        item['conversation_id'], user_id=DEFAULT_USER_ID
                    )
                )
                for item in conversations
            }
        return [
            {
                **item,
                'favorite_count': counts.get(item['conversation_id'], 0),
            }
            for item in conversations
        ]

    def _session(self, token):
        session = self.sessions.get(token)
        if session is None:
            raise ValueError('Session expired. Start a new conversation.')
        return session

    @staticmethod
    def _listing_key(payload):
        listing_key = payload.get('listing_key')
        if not isinstance(listing_key, str) or not 1 <= len(listing_key) <= 500:
            raise ValueError('Invalid listing key.')
        return listing_key

    def _favorites(self):
        favorites = getattr(self.orchestrator, 'favorites', None)
        if favorites is None:
            raise ValueError('Favorites are unavailable.')
        return favorites

    def list_favorites(self, token):
        session = self._session(token)
        favorites = getattr(self.orchestrator, 'favorites', None)
        if favorites is None:
            return []
        return favorites.list(
            session['conversation_id'], user_id=DEFAULT_USER_ID
        )

    def add_favorite(self, token, payload):
        session = self._session(token)
        listing_key = self._listing_key(payload)
        if listing_key not in session['cards']:
            raise ValueError('This home is not available in the current conversation.')
        return self._favorites().add(
            session['conversation_id'],
            user_id=DEFAULT_USER_ID,
            listing_key=listing_key,
        )

    def remove_favorite(self, token, payload):
        session = self._session(token)
        listing_key = self._listing_key(payload)
        removed = self._favorites().remove(
            session['conversation_id'],
            user_id=DEFAULT_USER_ID,
            listing_key=listing_key,
        )
        return {'listing_key': listing_key, 'removed': removed}

    @staticmethod
    def _message_id(payload):
        message_id = payload.get('message_id')
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 100:
            raise ValueError('Invalid message ID.')
        return message_id

    @staticmethod
    def _stopped(status):
        return dict(phase=status, status=status, cards=[], confirmation=None,
                    assistant_response=('Search stopped. You can update your requirements or search again.'
                        if status == 'cancelled' else
                        'This request timed out and was stopped. Please retry or narrow your search.'))

    async def cancel(self, token, payload):
        """Do not wait for the session lock; cancel the actual task and record early-arriving cancellations to guard against request races."""
        session = self._session(token)
        message_id = self._message_id(payload)
        if message_id in session['replies']:
            return {'status': 'completed'}
        session['cancelled'].add(message_id)
        active = session['active']
        if active and active[0] == message_id:
            active[1].cancel()
            try:
                await active[1]
            except (asyncio.CancelledError, Exception):
                pass
        return {'status': 'cancelled'}

    async def progress(self, token, payload):
        """Return the true A/B/C stage of the current request without exposing inputs, model outputs, or internal reasoning."""
        session = self._session(token)
        message_id = self._message_id(payload)
        current = session.get('progress')
        if not current or current['message_id'] != message_id:
            return {'status': 'idle', 'label': 'Starting your search…'}
        spans = current['trace']['spans']
        if not spans:
            return {'status': 'starting', 'label': 'Starting your search…'}
        running = [span for span in spans if span['status'] == 'running']
        span = running[-1] if running else spans[-1]
        return {
            'status': span['status'],
            'label': self.PROGRESS_LABELS.get(
                (span['stage'], span['operation']),
                'Working on your search…',
            ),
        }

    async def turn(self, token, payload):
        session = self._session(token)
        message_id = self._message_id(payload)
        async with session['lock']:
            if message_id in session['replies']:
                return session['replies'][message_id]
            if message_id in session['cancelled']:
                return self._stopped('cancelled')
            task = asyncio.create_task(self._turn(token, payload))
            session['active'] = (message_id, task)
            try:
                async with asyncio.timeout(self.turn_timeout_seconds):
                    return await task
            except TimeoutError:
                data = self._stopped('timed_out')
            except asyncio.CancelledError:
                data = self._stopped('cancelled')
            finally:
                session['active'] = None
            session['replies'][message_id] = data
            return data

    async def _turn(self, token, payload):
        session = self.sessions.get(token)
        if session is None:
            raise ValueError('Session expired. Start a new conversation.')
        text = payload.get('text', '')
        message_id = payload.get('message_id', '')
        keys = payload.get('selected_listing_keys', [])
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise ValueError('Please enter a message of 1–6000 characters.')
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 100:
            raise ValueError('Invalid message ID.')
        if not isinstance(keys, list) or len(keys) > 6 or any(not isinstance(k, str) or k not in session['cards'] for k in keys):
            raise ValueError('Select up to six homes from this conversation.')
        if message_id in session['replies']:
            return session['replies'][message_id]
        if self.orchestrator is None:
            raise ValueError('Preview mode. Start the server with --live to connect your configured A → B → C runtime.')
        cid = session['conversation_id']
        chat = getattr(self.orchestrator, 'chat', None)
        if chat is not None and hasattr(chat, 'set_conversation_title'):
            chat.ensure_conversation(cid, user_id=DEFAULT_USER_ID)
            chat.set_conversation_title(cid, user_id=DEFAULT_USER_ID, title=text)
        config = {'configurable': {'thread_id': cid}}
        before = (await self.orchestrator.a_graph.aget_state(config)).values or {}
        confirmed_title = None
        if payload.get('confirmation_id'):
            confirmation = before.get('confirmation') or {}
            if confirmation.get('confirmation_id') != payload['confirmation_id'] or confirmation.get('status') != 'pending' or confirmation.get('profile_version') != (before.get('profile') or {}).get('version'):
                raise ValueError('Your requirements have changed. Confirm the latest version.')
            confirmed_title = ' '.join(str(confirmation.get('summary') or '').split())
            text = 'confirm'
        if keys:
            # The existing handle_message only supports text; build this round's reference context from a trusted snapshot.
            context = [session['cards'][k] for k in dict.fromkeys(keys)]
            text += '\n\nSelected homes for this question (reference data, not new requirements):\n' + json.dumps(context, ensure_ascii=False)
        from property_agent.evaluation_trace import capture_trace

        with capture_trace(message_id) as trace:
            session['progress'] = {'message_id': message_id, 'trace': trace}
            result = await self.orchestrator.handle_message(text, conversation_id=cid, client_message_id=f'{cid}:{message_id}')
        data = asdict(result)
        state = (await self.orchestrator.a_graph.aget_state(config)).values or {}
        data['profile'] = state.get('profile')
        data['confirmation'] = state.get('confirmation') if state.get('status') == 'awaiting_confirmation' else None
        if state.get('status') != 'awaiting_clarification' and result.phase != 'b_clarification':
            data['clarification_questions'] = []
        data['cards'] = []
        if result.recommendation and result.run_id:
            data['cards'] = await self._recommendation_cards(
                session,
                run_id=result.run_id,
                recommendation=result.recommendation,
            )
        if confirmed_title and chat is not None and hasattr(chat, 'set_conversation_title'):
            if not confirmed_title.endswith(('.', '!', '?')):
                confirmed_title += '.'
            chat.set_conversation_title(
                cid,
                user_id=DEFAULT_USER_ID,
                title=confirmed_title,
                overwrite=True,
            )
        session['replies'][message_id] = data
        return data
