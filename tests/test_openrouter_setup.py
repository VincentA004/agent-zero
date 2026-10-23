import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_zero.config import Config
from agent_zero.engines import OpenRouter
from agent_zero.openrouter_setup import FREE_MODEL, configure, load_key
from agent_zero.runtime import Runtime


class FreeOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(Config(root=self.root,cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))

    async def asyncTearDown(self):
        await self.runtime.processes.interrupt_all()
        self.runtime.store.db.close()
        self.temp.cleanup()

    async def test_only_fixed_zero_price_route_allowed_before_networking(self):
        self.assertEqual(self.runtime.model,FREE_MODEL)
        paid = self.runtime.registry['cloud'][0]
        with patch('agent_zero.engines.request_json') as request, self.assertRaises(PermissionError):
            await self.runtime.cloud.chat(paid['id'],[],paid)
        request.assert_not_called()

    async def test_free_request_has_zero_ceiling_no_paid_fallback_and_zero_budget(self):
        free = next(m for m in self.runtime.registry['cloud'] if m['id']==FREE_MODEL)
        response = {'choices':[{'message':{'content':'Free response'}}],'usage':{'cost':0,'total_tokens':10}}
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture'}),patch('agent_zero.engines.request_json',return_value=response) as request:
            result = await self.runtime.cloud.chat(FREE_MODEL,[{'role':'user','content':'Hi'}],free)
        self.assertEqual(result,'Free response')
        payload=request.call_args.args[1]
        self.assertEqual(payload['provider']['max_price'],{'prompt':0,'completion':0,'request':0,'image':0})
        self.assertFalse(payload['provider']['allow_fallbacks'])
        self.assertEqual(self.runtime.cloud.spent,0)

    async def test_authenticated_operator_cannot_accidentally_select_paid_model(self):
        self.runtime.auth.setup('operator test passphrase')
        token=(await self.runtime.dispatch('sys_auth',{'passcode':'operator test passphrase'}))['token']
        with self.assertRaises(PermissionError):
            await self.runtime.dispatch('sys_model_swap',{'target_model_id':self.runtime.registry['cloud'][0]['id']},token)
        self.assertEqual(self.runtime.model,FREE_MODEL)

    async def test_record_must_match_selected_model(self):
        free=next(m for m in self.runtime.registry['cloud'] if m['id']==FREE_MODEL)
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture'}),patch('agent_zero.engines.request_json') as request,self.assertRaises(ValueError):
            await self.runtime.cloud.chat('other:free',[],free)
        request.assert_not_called()

    async def test_unexpected_billing_blocks_subsequent_requests(self):
        free=next(m for m in self.runtime.registry['cloud'] if m['id']==FREE_MODEL)
        response={'choices':[{'message':{'content':'Unexpected charge'}}],'usage':{'cost':.01}}
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture'}),patch('agent_zero.engines.request_json',return_value=response) as request:
            with self.assertRaises(RuntimeError):
                await self.runtime.cloud.chat(FREE_MODEL,[],free)
            with self.assertRaises(PermissionError):
                await self.runtime.cloud.chat(FREE_MODEL,[],free)
        self.assertEqual(request.call_count,1)


class SetupTests(unittest.TestCase):
    def test_hidden_setup_preserves_voice_and_stores_key_privately(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'config.json').write_text(json.dumps({'stt_model_path':'/local/model'}))
            config=Config(root=root)
            with patch('agent_zero.openrouter_setup.verify_key') as verify:
                configure(config,'fixture-api-key')
            verify.assert_called_once_with('fixture-api-key')
            stored=Config.load(root)
            self.assertTrue(stored.cloud_enabled)
            self.assertTrue(stored.cloud_free_only)
            self.assertEqual(stored.session_budget_usd,0)
            self.assertEqual(stored.stt_model_path,'/local/model')
            self.assertEqual((root/'openrouter.key').stat().st_mode & 0o777,0o600)
            with patch.dict(os.environ,{'OPENROUTER_API_KEY':'stale-environment-key'}):
                self.assertEqual(load_key(stored),'fixture-api-key')
            self.assertNotIn('fixture-api-key',(root/'config.json').read_text())

    def test_invalid_key_never_changes_config_or_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            original='{"cloud_enabled":false}'
            (root/'config.json').write_text(original)
            with patch('agent_zero.openrouter_setup.verify_key',side_effect=ValueError('HTTP 401')),self.assertRaises(ValueError):
                configure(Config(root=root),'invalid-key')
            self.assertEqual((root/'config.json').read_text(),original)
            self.assertFalse((root/'openrouter.key').exists())

    def test_zero_budget_requires_free_only_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                Config(root=Path(temp),session_budget_usd=0)

class ProviderErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_200_upstream_overload_is_bounded_and_retried_only_free(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Config(root=Path(temp),cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))
            free=next(m for m in runtime.registry['cloud'] if m['id']==FREE_MODEL)
            overloaded={'error':{'code':503,'message':'Temporarily overloaded'}}
            success={'choices':[{'message':{'content':'Recovered'}}],'usage':{'cost':0}}
            try:
                with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture'}),patch('agent_zero.engines.request_json',side_effect=[overloaded,overloaded,success]) as request,patch('agent_zero.engines.asyncio.sleep',new_callable=AsyncMock) as sleep:
                    result=await runtime.cloud.chat(FREE_MODEL,[],free)
                self.assertEqual(result,'Recovered')
                self.assertEqual(request.call_count,3)
                self.assertEqual(sleep.await_count,2)
            finally:
                runtime.store.db.close()

    async def test_quota_errors_do_not_retry_or_turn_into_missing_choices(self):
        from agent_zero.engines import ProviderError
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Config(root=Path(temp),cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))
            free=next(m for m in runtime.registry['cloud'] if m['id']==FREE_MODEL)
            try:
                with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture'}),patch('agent_zero.engines.request_json',return_value={'error':{'code':429,'message':'Rate limited'}}) as request,self.assertRaises(ProviderError):
                    await runtime.cloud.chat(FREE_MODEL,[],free)
                self.assertEqual(request.call_count,1)
            finally:
                runtime.store.db.close()

    async def test_exhausted_free_overload_can_recover_on_later_request(self):
        from unittest.mock import AsyncMock
        from agent_zero.engines import ProviderError
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Config(root=Path(temp),cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))
            free=next(m for m in runtime.registry['cloud'] if m['id']==FREE_MODEL)
            overloaded={'error':{'code':503,'message':'Temporarily overloaded'}}
            healthy={'choices':[{'message':{'content':'Recovered later'}}],'usage':{'cost':0}}
            try:
                with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture'}),patch('agent_zero.engines.request_json',side_effect=[overloaded,overloaded,overloaded,healthy]) as request,patch('agent_zero.engines.asyncio.sleep',new_callable=AsyncMock):
                    with self.assertRaises(ProviderError):
                        await runtime.cloud.chat(FREE_MODEL,[],free)
                    self.assertEqual(request.call_count,3)
                    self.assertEqual(await runtime.cloud.chat(FREE_MODEL,[],free),'Recovered later')
                self.assertEqual(request.call_count,4)
                self.assertEqual(runtime.cloud.spent,0)
                self.assertFalse(runtime.cloud.billing_fault)
            finally:
                runtime.store.db.close()

    async def test_key_redaction_happens_before_error_message_truncation(self):
        import io
        import urllib.error
        from agent_zero.engines import ProviderError,request_json
        secret='fixture-'+('S'*100)
        message=('x'*280)+secret
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Config(root=Path(temp),cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))
            free=next(m for m in runtime.registry['cloud'] if m['id']==FREE_MODEL)
            try:
                with patch.dict(os.environ,{'OPENROUTER_API_KEY':secret}),patch('agent_zero.engines.request_json',return_value={'error':{'code':401,'message':message}}):
                    with self.assertRaises(ProviderError) as caught:
                        await runtime.cloud.chat(FREE_MODEL,[],free)
                    self.assertNotIn('fixture-',str(caught.exception))
                body=io.BytesIO(json.dumps({'error':{'message':message}}).encode())
                error=urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions',401,'Unauthorized',{},body)
                with patch('agent_zero.engines.urllib.request.urlopen',side_effect=error):
                    with self.assertRaises(ProviderError) as caught:
                        request_json('https://openrouter.ai/api/v1/chat/completions',{}, {'Authorization':'Bearer '+secret})
                    self.assertNotIn('fixture-',str(caught.exception))
            finally:
                runtime.store.db.close()
