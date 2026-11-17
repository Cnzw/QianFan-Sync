"""Exercise the actual entry helper without uploading or publishing anything."""
import ast
import asyncio
from pathlib import Path
import re
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, Mock

SOURCE = Path(__file__).resolve().parents[1] / 'impl/youtube/platform.py'


class UploadEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_open_upload_dialog')
        namespace = dict(logger=Mock(), _msg=lambda x: x, Path=Path, BASE_DIR=Path(self.temp.name), re=re, uuid=uuid)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<entry>', 'exec'), namespace)
        self.open = namespace['_open_upload_dialog']
        self.page = Mock(url='https://studio.youtube.com/channel/test')
        self.file = Mock()
        self.file.first = self.file
        self.file.wait_for = AsyncMock()
        self.file.count = AsyncMock(return_value=0)
        self.button = Mock()
        self.button.first = self.button
        self.button.wait_for = AsyncMock()
        self.button.click = AsyncMock()
        self.button.filter.return_value = self.button
        self.page.locator.side_effect = lambda s: self.file if s == 'input[name="Filedata"]' else self.button
        self.page.get_by_text.return_value = self.button
        self.page.screenshot = AsyncMock()

    async def test_existing_hidden_file_input_needs_no_click(self):
        await self.open(None, self.page)
        self.button.click.assert_not_awaited()
        self.file.wait_for.assert_awaited_once_with(state='attached', timeout=1000)

    async def test_direct_entry_waits_for_actual_input_not_old_visible_picker(self):
        self.file.wait_for.side_effect = [TimeoutError(), None]
        await self.open(None, self.page)
        self.button.click.assert_awaited_once_with(timeout=5000)
        self.assertTrue(all('select-files-button' not in c.args[0] for c in self.page.locator.call_args_list))
        self.assertTrue(all(c.kwargs['state'] == 'attached' for c in self.file.wait_for.call_args_list))

    async def test_create_menu_fallback(self):
        self.file.wait_for.side_effect = [TimeoutError(), TimeoutError(), None]
        await self.open(None, self.page)
        self.assertEqual(self.button.click.await_count, 3)
        self.page.get_by_text.assert_called_once()

    async def test_login_redirect_reports_login_problem(self):
        self.file.wait_for.side_effect = TimeoutError()
        self.page.url = 'https://accounts.google.com/signin'
        with self.assertRaisesRegex(RuntimeError, '登录状态'):
            await self.open(None, self.page)
        self.button.click.assert_not_awaited()

    async def test_failure_saves_diagnostics_without_upload(self):
        self.file.wait_for.side_effect = TimeoutError()
        with self.assertRaisesRegex(RuntimeError, '尚未选择视频文件'):
            await self.open(None, self.page)
        self.page.screenshot.assert_awaited_once()
        self.file.set_input_files.assert_not_called()

    def test_final_automatic_done_click_only_in_auto_mode(self):
        """最终提交按「发布提交方式」设置分流（b783b29）。

        - 人工验收（manual_review_enabled() 为真）分支必须等待用户亲自提交：
          wait_for_manual_publish(page, 'youtube')
        - done 自动点击只允许存在于自动提交（为假）的 else 分支内，
          验收模式下不可触达。
        """
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))

        def is_call(node, func_name):
            return (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == func_name)

        review_ifs = [n for n in ast.walk(tree)
                      if isinstance(n, ast.If) and is_call(n.test, 'manual_review_enabled')]
        self.assertTrue(review_ifs, 'youtube 最终提交缺少 manual_review_enabled 分流')

        in_review_body, in_auto_else = set(), set()
        for node in review_ifs:
            for sub in node.body:
                in_review_body.update(id(n) for n in ast.walk(sub))
            for sub in node.orelse:
                in_auto_else.update(id(n) for n in ast.walk(sub))

        # 人工验收分支：等待用户亲自提交（youtube 专属成功信号在
        # wait_for_manual_publish 内按 share dialog 检测）
        gated = any(
            isinstance(n, ast.Await)
            and is_call(n.value, 'wait_for_manual_publish')
            and any(isinstance(a, ast.Constant) and a.value == 'youtube'
                    for a in n.value.args)
            for node in review_ifs
            for sub in node.body
            for n in ast.walk(sub)
        )
        self.assertTrue(gated, "人工验收分支必须调用 wait_for_manual_publish(page, 'youtube')")

        # done 自动点击：必须存在（自动提交默认行为），且只能在 else 分支内
        done_clicks = [n for n in ast.walk(tree)
                       if isinstance(n, ast.Await)
                       and isinstance(n.value, ast.Call)
                       and isinstance(n.value.func, ast.Attribute)
                       and n.value.func.attr == 'click'
                       and isinstance(n.value.func.value, ast.Name)
                       and n.value.func.value.id == 'done_btn']
        self.assertTrue(done_clicks, '自动提交分支的 done 点击丢失')
        for n in done_clicks:
            self.assertIn(id(n), in_auto_else,
                          'done 自动点击必须只存在于 manual_review_enabled() 为假的 else 分支内')


if __name__ == '__main__':
    unittest.main()
