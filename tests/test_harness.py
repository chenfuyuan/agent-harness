#!/usr/bin/env python3
"""母仓全链路自动化回归测试套件（Tests for ws CLI, Guards & Prototype Runtime）。

特点：通过 WS_REGISTRY_FILE 与 WS_SANDBOX_DIR 环境变量完全隔离在临时目录内运行，对母仓工作树零污染。
"""
from __future__ import annotations
import json
import os
import re
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent
WS_BIN = HUB / "bin" / "ws"
MODE_GUARD = HUB / "harness" / "guards" / "mode_guard.py"
READ_GUARD = HUB / "harness" / "guards" / "read_guard.py"
SECRETS_GUARD = HUB / "harness" / "guards" / "check_secrets.py"
HARNESS_GUARD = HUB / "harness" / "guards" / "harness_guard.py"
PROTO_CHECKER = HUB / "harness" / "guards" / "check_prototype.mjs"


class HarnessTestSuite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.reg_file = self.tmp_path / "ws-registry.json"
        self.sandbox_dir = self.tmp_path / "sandbox"
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.check_fb_dir = self.tmp_path / "check-fallback"
        self.check_fb_dir.mkdir(parents=True, exist_ok=True)
        self.env = {
            **os.environ,
            "WS_REGISTRY_FILE": str(self.reg_file),
            "WS_SANDBOX_DIR": str(self.sandbox_dir),
            "WS_CHECK_FALLBACK_DIR": str(self.check_fb_dir),
            "GIT_AUTHOR_NAME": "Test Bot",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test Bot",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        # 钩子片段优先读 WS_HUB_ROOT；清掉外部环境里的值与父级 pre-commit 注入的 GIT_DIR/GIT_INDEX_FILE，避免干扰临时仓 git worktree
        self.env.pop("WS_HUB_ROOT", None)
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY"):
            self.env.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, cmd: list[str], cwd: Path | None = None, stdin_text: str | None = None):
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            input=stdin_text,
            text=True,
            capture_output=True,
            env=self.env,
        )

    def claude_hook(self, proj: Path, tool: str, tool_input: dict, event: str = "PreToolUse", tool_use_id: str = "") -> dict:
        """按 Claude Code 钩子协议调用 mode_guard，返回其 JSON 输出。"""
        res = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "hook_event_name": event,
                "cwd": str(proj),
                "tool_name": tool,
                "tool_input": tool_input,
                "tool_use_id": tool_use_id,
            }),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def claude_decision(self, proj: Path, tool: str, tool_input: dict) -> str:
        """事前拦截的裁决：'allow' | 'ask' | 'deny'。"""
        out = self.claude_hook(proj, tool, tool_input)
        return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    def hub_ref_target(self, proj: Path) -> Path:
        """把业务仓 AGENTS.md 里的 Hub-Ref（`~/...`、绝对路径或相对项目根的路径）解析成绝对路径。"""
        m = re.search(r"Hub-Ref\*\*: `([^`]+)`", (proj / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIsNotNone(m, "AGENTS.md 缺少 Hub-Ref 行")
        ref = Path(m.group(1)).expanduser()
        return (ref if ref.is_absolute() else proj / ref).resolve()

    def test_01_greenfield_init_sync_link_doctor(self):
        proj = self.tmp_path / "demo-app"
        res = self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--name", "演示系统"])
        self.assertEqual(res.returncode, 0, res.stderr)

        # 检查三端文件、原型胶囊与单技能软链接
        self.assertTrue((proj / "AGENTS.md").is_file())
        self.assertTrue((proj / "CLAUDE.md").is_file())
        self.assertTrue((proj / "design" / "prototype" / "dev-overlay.js").is_file())
        self.assertTrue((proj / ".agents" / "hooks.json").is_file())
        self.assertTrue((proj / ".claude" / "settings.local.json").is_file())
        self.assertTrue((proj / ".claude" / "skills").is_symlink())
        gi_text = (proj / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".githooks/", gi_text)
        self.assertIn("!.env.sample", gi_text)
        # 母仓技能软链接是本机产物：写进忽略清单，不随仓库入库
        self.assertIn(".agents/skills/explore-architect", gi_text.splitlines())

        for skill in (
            "explore-architect",
            "bespoke-prototype",
            "tech-architect",
            "slice-build",
            "systematic-debugging",
            "adversarial-review",
            "compound-learn",
        ):
            s_link = proj / ".agents" / "skills" / skill
            self.assertTrue(s_link.is_symlink(), f"{skill} 应为软链接")
            self.assertTrue((s_link / "SKILL.md").is_file(), f"{skill}/SKILL.md 应可读")
            # 临时目录不在 $HOME 下时，应使用绝对路径而非穿过 / 的相对路径
            if not str(proj.resolve()).startswith(str(Path.home().resolve())):
                self.assertTrue(os.path.isabs(os.readlink(s_link)))

        # 自定义私有技能保留 + 失效断链自动清理（无参 ws sync / ws link 走注册表）
        custom_skill = proj / ".agents" / "skills" / "my-local-skill"
        custom_skill.mkdir(parents=True)
        (custom_skill / "SKILL.md").write_text("---\nname: my-local-skill\ndescription: local\n---\n", encoding="utf-8")
        stale_link = proj / ".agents" / "skills" / "obsolete-skill"
        stale_link.symlink_to("/nonexistent/obsolete-skill")

        sync_res = self.run_cmd(["python3", str(WS_BIN), "sync"], cwd=HUB)
        self.assertEqual(sync_res.returncode, 0, sync_res.stderr)
        self.assertFalse(stale_link.is_symlink())
        self.assertTrue((custom_skill / "SKILL.md").is_file())
        self.assertFalse((HUB / "harness" / "skills" / "my-local-skill").exists())

        link_res = self.run_cmd(["python3", str(WS_BIN), "link"], cwd=HUB)
        self.assertEqual(link_res.returncode, 0, link_res.stderr)

        doc_res = self.run_cmd(["python3", str(WS_BIN), "doctor", str(proj)])
        self.assertEqual(doc_res.returncode, 0, doc_res.stdout + doc_res.stderr)

        # 支持进入新建目录后省略 project_dir 直接执行 ws init --name "..."
        cwd_proj = self.tmp_path / "cwd-inited-app"
        cwd_proj.mkdir(parents=True)
        cwd_init_res = self.run_cmd(["python3", str(WS_BIN), "init", "--name", "当前目录项目"], cwd=cwd_proj)
        self.assertEqual(cwd_init_res.returncode, 0, cwd_init_res.stderr)
        self.assertIn("# 当前目录项目", (cwd_proj / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertTrue((cwd_proj / "design" / "prototype" / "dev-overlay.js").is_file())

    def test_02_brownfield_existing_repo_non_destructive(self):
        proj = self.tmp_path / "legacy-workbench"
        proj.mkdir(parents=True)
        self.run_cmd(["git", "init", "-b", "main"], cwd=proj)

        # 构造存量仓库：无 Mode 字段的 AGENTS.md、已有 CLAUDE.md、已有自定义 Claude 钩子与 Husky 钩子
        (proj / "AGENTS.md").write_text("# Legacy Workbench\n\nExisting team conventions.\n", encoding="utf-8")
        (proj / "CLAUDE.md").write_text("# Legacy Claude Notes\nKeep existing instructions.\n", encoding="utf-8")
        (proj / ".claude").mkdir()
        (proj / ".claude" / "settings.local.json").write_text(
            json.dumps({
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo custom-audit"}]}]
                },
            }),
            encoding="utf-8",
        )
        (proj / ".husky").mkdir()
        (proj / ".husky" / "pre-commit").write_text("#!/bin/sh\necho husky-lint\n", encoding="utf-8")

        res = self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])
        self.assertEqual(res.returncode, 0, res.stderr)

        # 1) 存量 AGENTS.md 必须真实注入 Mode: design 且门禁生效
        agents_txt = (proj / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("- **Mode**: `design`", agents_txt)
        self.assertIn("Existing team conventions.", agents_txt)

        block_check = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "write_file", "args": {"Path": str(proj / "src" / "legacy.py")}},
            }),
        )
        self.assertEqual(json.loads(block_check.stdout)["decision"], "deny")

        # 2) CLAUDE.md 自动前置 @AGENTS.md 且不丢原文
        claude_txt = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude_txt.startswith("@AGENTS.md"))
        self.assertIn("Keep existing instructions.", claude_txt)

        # 3) .claude/settings.local.json 保留原有钩子，同时挂载 mode_guard 与 read_guard
        c_cfg = json.loads((proj / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(len(c_cfg["hooks"]["PreToolUse"]), 3)
        pre_dump = json.dumps(c_cfg["hooks"]["PreToolUse"])
        self.assertIn("mode_guard.py", pre_dump)
        self.assertIn("read_guard.py", pre_dump)
        self.assertEqual(c_cfg["permissions"]["allow"], ["Read"])

        # 4) Husky 钩子保留原有逻辑并注入守卫块；钩子文件通常会入库，片段里不得出现本机母仓路径
        husky_txt = (proj / ".husky" / "pre-commit").read_text(encoding="utf-8")
        self.assertIn("echo husky-lint", husky_txt)
        self.assertIn("ws-harness-guard:start", husky_txt)
        self.assertNotIn(str(HUB), husky_txt)

        # 4b) 宿主钩子没有 `set -e` 时，门禁拦截也必须让提交失败（不能只打印警告却返回 0）；放行时宿主原有逻辑照常执行
        (proj / "src").mkdir()
        (proj / "src" / "legacy.py").write_text("x = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "src/legacy.py"], cwd=proj)
        husky_run = self.run_cmd(["sh", ".husky/pre-commit"], cwd=proj)
        self.assertNotEqual(husky_run.returncode, 0, husky_run.stdout + husky_run.stderr)
        self.assertIn("src/legacy.py", husky_run.stderr)
        self.run_cmd(["git", "rm", "--cached", "-q", "src/legacy.py"], cwd=proj)
        husky_pass = self.run_cmd(["sh", ".husky/pre-commit"], cwd=proj)
        self.assertEqual(husky_pass.returncode, 0, husky_pass.stderr)
        self.assertIn("husky-lint", husky_pass.stdout)

        # 4c) 钩子文件写好但 Git 不会调用（core.hooksPath 未指向 Husky）时，doctor 必须如实报错而不是显示 OK
        doc_inactive = self.run_cmd(["python3", str(WS_BIN), "doctor", str(proj)])
        self.assertEqual(doc_inactive.returncode, 1, doc_inactive.stdout)
        self.assertIn("core.hooksPath", doc_inactive.stdout)
        self.run_cmd(["git", "config", "core.hooksPath", ".husky"], cwd=proj)
        doc_active = self.run_cmd(["python3", str(WS_BIN), "doctor", str(proj)])
        self.assertEqual(doc_active.returncode, 0, doc_active.stdout + doc_active.stderr)

        # 5) ws mode --slice 能正常更新 Active-Slice
        m_res = self.run_cmd(["python3", str(WS_BIN), "mode", "build", str(proj), "--slice", "design/slices/01-core.md"])
        self.assertEqual(m_res.returncode, 0, m_res.stderr)
        self.assertIn("`design/slices/01-core.md`", (proj / "AGENTS.md").read_text(encoding="utf-8"))

    def test_03_module_agents_md_does_not_bypass_mode_guard(self):
        proj = self.tmp_path / "trading-app"
        self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--mode", "build"])

        mod_dir = proj / "src" / "orders"
        mod_dir.mkdir(parents=True)
        (mod_dir / "AGENTS.md").write_text("# Module: orders\n- 唯一公开入口: ./index.ts\n", encoding="utf-8")
        (mod_dir / "index.ts").write_text("export const ok = 1;\n", encoding="utf-8")
        self.run_cmd(["git", "add", "."], cwd=proj)
        self.run_cmd(["git", "commit", "-m", "init orders module"], cwd=proj)

        # 回切 Mode: design 探索新大模块
        self.run_cmd(["python3", str(WS_BIN), "mode", "design", str(proj)])

        # agy 尝试写含模块 AGENTS.md 的子目录源码 -> 必须 deny（不被子目录 AGENTS.md 劫持）
        agy_res = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "edit_file", "args": {"path": str(mod_dir / "index.ts"), "Replacement": "bad"}},
            }),
        )
        self.assertEqual(json.loads(agy_res.stdout)["decision"], "deny")

        # Claude 尝试写子模块源码 -> 必须 deny
        claude_res = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "hook_event_name": "PreToolUse",
                "cwd": str(proj),
                "tool_name": "Edit",
                "tool_input": {"file_path": str(mod_dir / "index.ts"), "new_string": "bad"},
            }),
        )
        self.assertEqual(json.loads(claude_res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

        # design/ 下的中文文件名（git 默认会转义加引号输出）不得被误判成业务代码
        research = proj / "design" / "research"
        research.mkdir(parents=True)
        (research / "行业调研.md").write_text("# 调研\n", encoding="utf-8")
        self.run_cmd(["git", "add", "design/research"], cwd=proj)
        cjk_res = self.run_cmd(["python3", str(MODE_GUARD), "--git-staged", "--cwd", str(proj)], cwd=proj)
        self.assertEqual(cjk_res.returncode, 0, cjk_res.stderr)

        # Git 暂存删除生产文件（git rm）-> pre-commit 必须拦截
        self.run_cmd(["git", "rm", "src/orders/index.ts"], cwd=proj)
        git_del_res = self.run_cmd(["python3", str(MODE_GUARD), "--git-staged", "--cwd", str(proj)], cwd=proj)
        self.assertEqual(git_del_res.returncode, 1)

        # 尝试顺着软链接覆写母仓共享技能 -> 必须 deny
        symlink_skill_md = proj / ".agents" / "skills" / "explore-architect" / "SKILL.md"
        sym_res = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "write_file", "args": {"Path": str(symlink_skill_md), "Content": "overwrite"}},
            }),
        )
        self.assertEqual(json.loads(sym_res.stdout)["decision"], "deny")

    def test_04_self_unlock_hook_tamper_and_shell_bypass_guard(self):
        proj = self.tmp_path / "guard-hardening"
        self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])

        # 1) 禁止篡改 .agents/hooks.json
        tamper_hook = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "write_file", "args": {"Path": str(proj / ".agents" / "hooks.json"), "Content": "{}"}},
            }),
        )
        self.assertEqual(json.loads(tamper_hook.stdout)["decision"], "deny")

        # 2) AI 试图编辑 AGENTS.md 自解锁 Mode: build -> 触发 force_ask / ask 人工确认
        self_unlock_agy = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {
                    "name": "edit_file",
                    "args": {"Path": str(proj / "AGENTS.md"), "Replacement": "- **Mode**: `build`"},
                },
            }),
        )
        self.assertEqual(json.loads(self_unlock_agy.stdout)["decision"], "force_ask")

        # 正常编辑 AGENTS.md 的非 Mode 说明（保持 Mode: design）允许；agy Append 追加与 PROJECT.md 根档案也放行
        normal_agents_edit = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {
                    "name": "edit_file",
                    "args": {"Path": str(proj / "AGENTS.md"), "Replacement": "- 仅做欧易 USDT 永续合约"},
                },
            }),
        )
        self.assertEqual(json.loads(normal_agents_edit.stdout)["decision"], "allow")

        append_agents = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": str(proj / "AGENTS.md"), "Content": "\n- 追加备注\n", "Append": True},
                },
            }),
        )
        self.assertEqual(json.loads(append_agents.stdout)["decision"], "allow")

        project_md_write = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": str(proj / "PROJECT.md"), "Content": "# 项目档案\n"},
                },
            }),
        )
        self.assertEqual(json.loads(project_md_write.stdout)["decision"], "allow")

        # 3) Mode: design 下拦截 Shell 写 src/，放行 /tmp 公开只读探针
        shell_write_src = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "run_command", "args": {"CommandLine": "mkdir -p src && echo 1 > src/app.ts"}},
            }),
        )
        self.assertEqual(json.loads(shell_write_src.stdout)["decision"], "deny")

        shell_probe_tmp = self.run_cmd(
            ["python3", str(MODE_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "run_command", "args": {"CommandLine": "echo '{}' > /tmp/okx_probe.json"}},
            }),
        )
        self.assertEqual(json.loads(shell_probe_tmp.stdout)["decision"], "allow")

        # 3b) 自解锁按“改完后的全文”判定：最小改动、整文件覆写都要升级为人工确认，普通编辑照常放行
        agents_md = str(proj / "AGENTS.md")
        self.assertEqual(
            self.claude_decision(proj, "Edit", {
                "file_path": agents_md,
                "old_string": "`design`\n- **Active-Slice**",
                "new_string": "`build`\n- **Active-Slice**",
            }),
            "ask",
        )
        self.assertEqual(
            self.claude_decision(proj, "Write", {"file_path": agents_md, "content": "# demo\n\n没有 Mode 行也没有宪章区块\n"}),
            "ask",
        )
        self.assertEqual(
            self.claude_decision(proj, "Edit", {
                "file_path": agents_md,
                "old_string": "- **Active-Slice**: `none`",
                "new_string": "- **Active-Slice**: `none`\n- 仅做只读行情",
            }),
            "allow",
        )

        # 3c) 终端命令按词元核对写入目标：看得穿 cd / $PWD / 根目录散文件，且不被引号里的箭头、相似目录名误伤
        shell_cases = [
            ("cd src && touch a.ts", "deny"),
            ('echo 1 > "$PWD/src/app.ts"', "deny"),
            ("echo x > main.py", "deny"),
            ("rm -rf src", "deny"),
            ("git commit --no-verify -m wip", "deny"),
            ("sed -i '' 's/`design`/`build`/' AGENTS.md", "ask"),
            ('echo "design -> app shell layout"', "allow"),
            ('grep -rn "x => lib" design/ 2>/dev/null', "allow"),
            ("mkdir -p design/research/api-notes", "allow"),
            ("git mv design/a.md design/b.md", "allow"),
            ('git commit -m "docs: explain the -n flag"', "allow"),
            ("cat > design/notes.md <<'EOF'\nrm -rf src 只是文档里的一句话\nEOF", "allow"),
        ]
        for cmd, expected in shell_cases:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.claude_decision(proj, "Bash", {"command": cmd}), expected)

        # 4) 母仓根目录下误执行无参 ws init / ws mode build 必须拒绝，不污染母仓 AGENTS.md
        hub_before = (HUB / "AGENTS.md").read_text(encoding="utf-8")
        hub_init_err = self.run_cmd(["python3", str(WS_BIN), "init", "--name", "误触"], cwd=HUB)
        self.assertEqual(hub_init_err.returncode, 1)
        hub_mode_err = self.run_cmd(["python3", str(WS_BIN), "mode", "build"], cwd=HUB)
        self.assertEqual(hub_mode_err.returncode, 1)
        self.assertEqual((HUB / "AGENTS.md").read_text(encoding="utf-8"), hub_before)

    def test_05_worktree_secrets_and_false_positive_immunity(self):
        proj = self.tmp_path / "sec-repo"
        proj.mkdir()
        self.run_cmd(["git", "init", "-b", "main"], cwd=proj)

        # 未 git add 的新建文件包含密钥：--worktree（及 ws check）必须检出！
        fake_key = "tvly-" + "A1b2C3d4E5f6G7h8I9j0"
        (proj / "src").mkdir()
        (proj / "src" / "untracked_client.py").write_text(f'KEY = "{fake_key}"\n', encoding="utf-8")
        wt_fail = self.run_cmd(["python3", str(WS_BIN), "check", str(proj)])
        self.assertEqual(wt_fail.returncode, 1)

        # 替换为正常的路由路径常量、Header 名与 .env.sample：零误报通过
        (proj / "src" / "untracked_client.py").write_text(
            'resetPasswordUrl = "/api/v1/auth/reset-password"\n'
            'csrfTokenHeader = "X-CSRF-Validation-Token"\n',
            encoding="utf-8",
        )
        (proj / ".env.sample").write_text("OKX_API_KEY=your_api_key_here\n", encoding="utf-8")
        self.run_cmd(["git", "add", ".env.sample", "src/untracked_client.py"], cwd=proj)
        wt_ok = self.run_cmd(["python3", str(WS_BIN), "check", str(proj)])
        self.assertEqual(wt_ok.returncode, 0, wt_ok.stderr)

        # 三类曾经漏报的写法必须检出：纯大写十六进制密钥、配置文件里不带引号的口令、连接串内嵌口令
        upper_hex = "A1B2C3D4E5F60718" + "293A4B5C6D7E8F90"
        (proj / "src" / "upper_hex.py").write_text(f'SECRET_KEY = "{upper_hex}"\n', encoding="utf-8")
        (proj / "src" / "config.yaml").write_text("db:\n  password: " + "hunter2hunter2" + "hunter2xyz\n", encoding="utf-8")
        (proj / "src" / "db.py").write_text(
            'DATABASE_URL = "postgres://admin:' + "S3cretPassw0rd99" + '@db.internal:5432/app"\n', encoding="utf-8"
        )
        leaks = self.run_cmd(["python3", str(SECRETS_GUARD), "--worktree", "--cwd", str(proj)])
        self.assertEqual(leaks.returncode, 1)
        for leaked_file in ("src/upper_hex.py", "src/config.yaml", "src/db.py"):
            self.assertIn(leaked_file, leaks.stderr)
        self.assertNotIn("S3cretPassw0rd99", leaks.stderr)  # 报告里不得回显口令本身

        # 对应的安全写法零误报：引用环境变量名的常量、变量插值的连接串、不含数字的普通配置值
        (proj / "src" / "upper_hex.py").write_text('apiKeySource = "OKX_API_SECRET_KEY_V5"\n', encoding="utf-8")
        (proj / "src" / "config.yaml").write_text("auth:\n  tokenAudience: my-internal-service-name\n", encoding="utf-8")
        (proj / "src" / "db.py").write_text('DATABASE_URL = "postgres://app:${DB_PASSWORD}@db:5432/app"\n', encoding="utf-8")
        clean = self.run_cmd(["python3", str(SECRETS_GUARD), "--worktree", "--cwd", str(proj)])
        self.assertEqual(clean.returncode, 0, clean.stderr)

    def test_06_anti_stub_multiline_diff_and_module_boundary(self):
        proj = self.tmp_path / "anti-stub-repo"
        proj.mkdir()
        self.run_cmd(["git", "init", "-b", "main"], cwd=proj)

        # 基线提交一个含历史遗留 TODO 的老文件（测试 --diff 存量兼容）
        (proj / "backend").mkdir()
        (proj / "backend" / "legacy.py").write_text("# TODO: old technical debt from 2025\nx = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "."], cwd=proj)
        self.run_cmd(["git", "commit", "-m", "legacy baseline"], cwd=proj)

        # 全量模式拦截老 TODO，--diff 增量模式放行未改动老文件
        self.assertEqual(self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)]).returncode, 1)
        self.assertEqual(self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"]).returncode, 0)

        # 1) 多行 Python except ...:\n pass 必须检出
        (proj / "backend" / "handler.py").write_text(
            "def run():\n    try:\n        work()\n    except Exception as exc:\n        # silent\n        pass\n",
            encoding="utf-8",
        )
        py_catch_fail = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(py_catch_fail.returncode, 1)
        self.assertIn("except", py_catch_fail.stderr)
        (proj / "backend" / "handler.py").unlink()

        # 2) JS 带注释空 catch 与空括号 stub-ok() 必须检出
        orders_dir = proj / "src" / "orders"
        ui_dir = proj / "src" / "ui"
        (orders_dir / "internal").mkdir(parents=True)
        ui_dir.mkdir(parents=True)
        (orders_dir / "AGENTS.md").write_text("# Module: orders\n- **唯一公开入口**：`./index.ts`\n", encoding="utf-8")
        (orders_dir / "index.ts").write_text("export { placeOrder } from './internal/repo';\n", encoding="utf-8")
        (orders_dir / "internal" / "repo.ts").write_text("export function placeOrder() { return true; }\n", encoding="utf-8")

        (ui_dir / "ticket.ts").write_text(
            "// TODO: sneak empty stub-ok()\n"
            "export function click() { try { go(); } catch (e) { /* noop */ } }\n",
            encoding="utf-8",
        )
        js_fail = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(js_fail.returncode, 1)
        self.assertIn("TODO", js_fail.stderr)
        self.assertIn("catch", js_fail.stderr)

        # 3) 跨模块绕过 ./index.ts 深层导入 internal/repo 必须检出
        (ui_dir / "ticket.ts").write_text(
            "import { placeOrder } from '../orders/internal/repo';\n"
            "export function click() { return placeOrder(); }\n",
            encoding="utf-8",
        )
        boundary_fail = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(boundary_fail.returncode, 1)
        self.assertIn("越权", boundary_fail.stderr)

        # 改为走公开入口 ../orders 后全绿通过
        (ui_dir / "ticket.ts").write_text(
            "// TODO: batch order support stub-ok(slice-03)\n"
            "import { placeOrder } from '../orders';\n"
            "export function click() { return placeOrder(); }\n",
            encoding="utf-8",
        )
        clean_res = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(clean_res.returncode, 0, clean_res.stderr)

        # 4) 契约里的入口不加反引号也要解析对（曾被解析成末尾字符 `s`，导致合法导入全报越权）
        (orders_dir / "AGENTS.md").write_text("# Module: orders\n- 唯一公开入口: ./index.ts\n", encoding="utf-8")
        bare_entry = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(bare_entry.returncode, 0, bare_entry.stderr)

        # 5) 行号与豁免都落在 import 所在行：前面有 export 语句时 `boundary-ok` 仍然生效
        deep_import = "import { placeOrder } from '../orders/internal/repo';"
        (ui_dir / "ticket.ts").write_text(
            f"export const VERSION = 1;\n{deep_import} // boundary-ok 迁移期豁免\nexport const go = () => placeOrder();\n",
            encoding="utf-8",
        )
        waived = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(waived.returncode, 0, waived.stderr)
        (ui_dir / "ticket.ts").write_text(
            f"export const VERSION = 1;\n{deep_import}\nexport const go = () => placeOrder();\n", encoding="utf-8"
        )
        unwaived = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(unwaived.returncode, 1)
        self.assertIn("src/ui/ticket.ts:2:", unwaived.stderr)

        # 6) 动态 import() 绕过公开入口同样检出
        (ui_dir / "ticket.ts").write_text(
            "export async function go() { const m = await import('../orders/internal/repo'); return m.placeOrder(); }\n",
            encoding="utf-8",
        )
        dynamic = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(dynamic.returncode, 1)
        self.assertIn("越权", dynamic.stderr)
        (ui_dir / "ticket.ts").unlink()

        # 7) 不叫 src/ 的顶层包目录也在扫描范围内；NotImplementedError 视为未完成实现，抽象方法声明除外
        pkg_dir = proj / "myapp"
        pkg_dir.mkdir()
        (pkg_dir / "pricing.py").write_text("def price():\n    raise NotImplementedError\n", encoding="utf-8")
        not_impl = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(not_impl.returncode, 1)
        self.assertIn("myapp/pricing.py:2:", not_impl.stderr)
        (pkg_dir / "pricing.py").write_text(
            "from abc import ABC, abstractmethod\n\n\nclass Pricer(ABC):\n"
            "    @abstractmethod\n    def price(self):\n        raise NotImplementedError\n",
            encoding="utf-8",
        )
        abstract_ok = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj), "--diff"])
        self.assertEqual(abstract_ok.returncode, 0, abstract_ok.stderr)
        self.assertRegex(abstract_ok.stdout, r"已扫描 \d+ 个源码文件")

        # 8) 没有任何源码可扫时如实说明，不把空扫描报成“检查通过”
        empty_proj = self.tmp_path / "empty-repo"
        (empty_proj / "design").mkdir(parents=True)
        (empty_proj / "design" / "sketch.js").write_text("// TODO: 原型草稿不属于生产代码\n", encoding="utf-8")
        empty_res = self.run_cmd(["python3", str(HARNESS_GUARD), str(empty_proj)])
        self.assertEqual(empty_res.returncode, 0, empty_res.stderr)
        self.assertIn("未发现可扫描的源码文件", empty_res.stdout)

    def test_07_prototype_overlay_and_checker(self):
        proj = self.tmp_path / "proto-app"
        self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])
        proto_dir = proj / "design" / "prototype"

        # 模拟 Step 1 比稿期（无 index.html，仅有 variant-a.html），同时测试 view×state 交集、flex 隐藏与尖括号转义
        (proto_dir / "variant-a.html").write_text(
            """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    .flex-box { display: flex; padding: 12px; }
  </style>
</head>
<body>
  <main id="app">
    <section class="flex-box" id="trade-normal" data-when-view="trade" data-when-state="normal">Trade Normal</section>
    <section class="flex-box" id="trade-empty" data-when-view="trade" data-when-state="empty">Trade Empty Guide</section>
    <section class="flex-box" id="review-any" data-when-view="review">Review Ledger</section>
  </main>
  <script>
    window.PROTO_SPEC = {
      title: '<OrderTerminal> 比稿 A',
      views: [{ id: 'trade', label: '交易' }, { id: 'review', label: '复盘' }],
      states: [{ id: 'normal', label: '持仓' }, { id: 'empty', label: '空仓' }],
      specs: [{ kind: 'contract', text: '当 <OrderTicket> 金额 < 100 时置灰' }]
    };
  </script>
  <script src="./dev-overlay.js"></script>
  <script>
    // 自验：切换到 review + empty 时，trade-empty 绝不能因 state=empty 被误显示，且 flex 容器必须真实隐藏
    window.addEventListener('load', () => {
      window.__DEV_OVERLAY__.setDrawerOpen(true);
      window.__DEV_OVERLAY__.setViewState('review', 'empty');
      const te = document.getElementById('trade-empty');
      if (!te.hidden || te.getClientRects().length !== 0) {
        throw new Error('view x state intersection or flex [hidden] failed');
      }
      const shadowRoot = document.getElementById('__proto_dev_overlay__').shadowRoot;
      const shadowText = shadowRoot.textContent;
      if (!shadowText.includes('<OrderTicket>') || !shadowText.includes('< 100')) {
        throw new Error('angle brackets were swallowed in spec drawer');
      }
      // macOS 上 Option+D 的 e.key 是 '∂'：快捷键必须按物理键位 KeyD 生效（再按一次恢复显示）
      const pressAltD = () => window.dispatchEvent(new KeyboardEvent('keydown', { altKey: true, code: 'KeyD', key: '∂' }));
      pressAltD();
      if (!shadowRoot.querySelector('.wrap').hidden) {
        throw new Error('Alt+D did not hide the overlay when e.key is not "d"');
      }
      pressAltD();
    });
  </script>
</body>
</html>
""",
            encoding="utf-8",
        )

        shots_dir = self.tmp_path / "shots"
        chk = self.run_cmd(["node", str(PROTO_CHECKER), str(proto_dir), "--shots", str(shots_dir)])
        if chk.returncode == 2 and "无法启动无头浏览器" in chk.stderr:
            self.skipTest("当前受限沙盒环境不允许启动 Chromium Mach 端口，跳过无头浏览器进程级断言")
        self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
        self.assertIn("遍历 4 个视图×状态组合", chk.stdout)
        self.assertTrue((shots_dir / "variant-a__trade__normal.png").is_file())

        # 单屏超过 12 个带底色静态状态胶囊时应输出去噪提醒（不阻断返回码 0）
        noisy_html = self.tmp_path / "noisy-pills.html"
        pills = "".join(f'<span class="tag" style="background:#e7f5ee;padding:2px 6px">ok-{i}</span>' for i in range(14))
        noisy_html.write_text(f'<!doctype html><html><body><main id="app">{pills}</main></body></html>', encoding="utf-8")
        noisy_chk = self.run_cmd(["node", str(PROTO_CHECKER), str(noisy_html)])
        self.assertEqual(noisy_chk.returncode, 0, noisy_chk.stdout + noisy_chk.stderr)
        self.assertIn("带底色状态胶囊", noisy_chk.stdout)

    def test_08_sandbox_promote_isolated(self):
        idea_dir = self.sandbox_dir / "alpha-radar"
        idea_dir.mkdir(parents=True)
        (idea_dir / "SYSTEM.md").write_text("# Alpha Radar System\n", encoding="utf-8")

        dst_repo = self.tmp_path / "graduated-alpha"
        res = self.run_cmd(["python3", str(WS_BIN), "promote", "alpha-radar", str(dst_repo)])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse(idea_dir.exists())
        self.assertTrue((dst_repo / "design" / "SYSTEM.md").is_file())
        self.assertTrue((dst_repo / "design" / "prototype" / "dev-overlay.js").is_file())
        self.assertTrue((dst_repo / "AGENTS.md").is_file())

        # 迁入已有仓库且存在同名文件：必须整体中止，沙盒与目标仓两边都原样保留
        idea2 = self.sandbox_dir / "beta-radar"
        (idea2 / "research").mkdir(parents=True)
        (idea2 / "SYSTEM.md").write_text("# 沙盒里的新骨架\n", encoding="utf-8")
        (idea2 / "research" / "notes.md").write_text("沙盒调研\n", encoding="utf-8")
        existing = self.tmp_path / "existing-repo"
        (existing / "design" / "research").mkdir(parents=True)
        (existing / "design" / "SYSTEM.md").write_text("# 目标仓里手写的骨架\n", encoding="utf-8")

        conflict = self.run_cmd(["python3", str(WS_BIN), "promote", "beta-radar", str(existing)])
        self.assertEqual(conflict.returncode, 1)
        self.assertIn("design/SYSTEM.md", conflict.stderr)
        self.assertEqual((existing / "design" / "SYSTEM.md").read_text(encoding="utf-8"), "# 目标仓里手写的骨架\n")
        self.assertTrue((idea2 / "research" / "notes.md").is_file())
        self.assertFalse((existing / "AGENTS.md").exists())

        # 去掉冲突文件后重试：同名目录合并迁入
        (existing / "design" / "SYSTEM.md").unlink()
        merged = self.run_cmd(["python3", str(WS_BIN), "promote", "beta-radar", str(existing)])
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertEqual((existing / "design" / "SYSTEM.md").read_text(encoding="utf-8"), "# 沙盒里的新骨架\n")
        self.assertTrue((existing / "design" / "research" / "notes.md").is_file())

    def test_09_post_shell_worktree_audit(self):
        proj = self.tmp_path / "audit-app"
        self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])
        opaque_cmd = {"command": "python3 /tmp/generate.py"}

        # 1) 事前看不穿的命令（脚本、脚手架）放行执行，但执行后工作区多出 design/ 之外的文件 -> 事后核对反馈给 AI
        self.assertEqual(self.claude_hook(proj, "Bash", opaque_cmd, tool_use_id="t1"), {})
        (proj / "src").mkdir()
        (proj / "src" / "generated.py").write_text("x = 1\n", encoding="utf-8")
        post = self.claude_hook(proj, "Bash", opaque_cmd, event="PostToolUse", tool_use_id="t1")
        self.assertEqual(post.get("decision"), "block")
        self.assertIn("src/generated.py", post["reason"])

        # 2) 命令执行前就已存在的改动（例如用户手上的工作）不算到这条命令头上；只写 design/ 也不触发
        self.assertEqual(self.claude_hook(proj, "Bash", opaque_cmd, tool_use_id="t2"), {})
        (proj / "design" / "notes.md").write_text("设计笔记\n", encoding="utf-8")
        self.assertEqual(self.claude_hook(proj, "Bash", opaque_cmd, event="PostToolUse", tool_use_id="t2"), {})

        # 3) 命令把 Mode 改成 build（事前没经过用户确认）-> 事后核对要求恢复
        agents_md = proj / "AGENTS.md"
        design_text = agents_md.read_text(encoding="utf-8")
        self.assertEqual(self.claude_hook(proj, "Bash", opaque_cmd, tool_use_id="t3"), {})
        agents_md.write_text(design_text.replace("- **Mode**: `design`", "- **Mode**: `build`"), encoding="utf-8")
        flipped = self.claude_hook(proj, "Bash", opaque_cmd, event="PostToolUse", tool_use_id="t3")
        self.assertEqual(flipped.get("decision"), "block")
        self.assertIn("Mode", flipped["reason"])
        agents_md.write_text(design_text, encoding="utf-8")

        # 4) 不依赖钩子的同款核对：`ws check` 在 design 模式下发现白名单外改动即失败，清理后通过
        gate_fail = self.run_cmd(["python3", str(WS_BIN), "check", str(proj)])
        self.assertEqual(gate_fail.returncode, 1)
        self.assertIn("src/generated.py", gate_fail.stderr)
        (proj / "src" / "generated.py").unlink()
        gate_ok = self.run_cmd(["python3", str(WS_BIN), "check", str(proj)])
        self.assertEqual(gate_ok.returncode, 0, gate_ok.stdout + gate_ok.stderr)

        # 5) ws init 已把事前 + 事后两个钩子都接入 Claude Code 配置
        hooks = json.loads((proj / ".claude" / "settings.local.json").read_text(encoding="utf-8"))["hooks"]
        self.assertIn("mode_guard.py", json.dumps(hooks["PreToolUse"]))
        self.assertEqual(hooks["PostToolUse"][-1]["matcher"], "Bash")
        self.assertIn("mode_guard.py", json.dumps(hooks["PostToolUse"]))

    def test_10_unparseable_hook_config_is_never_overwritten(self):
        proj = self.tmp_path / "jsonc-repo"
        (proj / ".claude").mkdir(parents=True)
        self.run_cmd(["git", "init", "-b", "main"], cwd=proj)
        settings = proj / ".claude" / "settings.local.json"
        original = '{\n  // 手写注释\n  "permissions": {"allow": ["Read"]},\n}\n'
        settings.write_text(original, encoding="utf-8")

        # 带注释/尾逗号的配置无法解析：初始化必须在动任何文件之前中止，原文件一字不改
        res = self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])
        self.assertEqual(res.returncode, 1)
        self.assertIn("settings.local.json", res.stderr)
        self.assertEqual(settings.read_text(encoding="utf-8"), original)
        self.assertFalse((proj / "AGENTS.md").exists())

        # 修正为合法 JSON 后正常接入，并保留原有权限配置
        settings.write_text('{"permissions": {"allow": ["Read"]}}\n', encoding="utf-8")
        ok = self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"], ["Read"])

    def test_11_hub_ref_resolution_and_sync_refresh(self):
        proj = self.tmp_path / "hubref-app"
        self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])
        agents_md = proj / "AGENTS.md"
        self.assertEqual(self.hub_ref_target(proj), HUB.resolve())

        # 母仓在主目录下时，入库的 AGENTS.md 里写 `~/...`，不出现本机用户名（主目录绝对路径）
        home = Path.home().resolve()
        if home in HUB.resolve().parents:
            agents_text = agents_md.read_text(encoding="utf-8")
            self.assertIn("- **Hub-Ref**: `~/", agents_text)
            self.assertNotIn(str(home), agents_text)

        def set_hub_ref(value: str):
            text = agents_md.read_text(encoding="utf-8")
            agents_md.write_text(re.sub(r"(Hub-Ref\*\*: )`[^`]+`", lambda m: f"{m.group(1)}`{value}`", text), encoding="utf-8")

        (proj / "src").mkdir()
        (proj / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "src/app.py"], cwd=proj)

        # 1) 默认写法（`~/...` 或绝对路径）与手写的相对项目根路径，提交钩子都能定位母仓并拦截
        for ref in (None, os.path.relpath(HUB.resolve(), proj.resolve())):
            with self.subTest(hub_ref=ref or "default"):
                if ref:
                    set_hub_ref(ref)
                blocked = self.run_cmd(["sh", ".githooks/pre-commit"], cwd=proj)
                self.assertNotEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
                self.assertIn("src/app.py", blocked.stderr)

        # 2) Hub-Ref 指向不存在的位置（母仓搬家）：不阻断提交，但必须给出看得见的警告而不是静默跳过
        set_hub_ref("/nonexistent/old-hub")
        skipped = self.run_cmd(["sh", ".githooks/pre-commit"], cwd=proj)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn("未找到工作流母仓", skipped.stderr)

        # 3) ws sync 把过期的 Hub-Ref 校正回当前母仓
        sync_res = self.run_cmd(["python3", str(WS_BIN), "sync", str(proj)])
        self.assertEqual(sync_res.returncode, 0, sync_res.stderr)
        self.assertEqual(self.hub_ref_target(proj), HUB.resolve())

    def test_12_guard_runs_before_host_hook_that_ends_with_exec(self):
        proj = self.tmp_path / "exec-hook-repo"
        (proj / ".githooks").mkdir(parents=True)
        self.run_cmd(["git", "init", "-b", "main"], cwd=proj)

        # 存量仓自带一份已入库的提交钩子，最后一行用 exec 把控制权整个交给自己的检查脚本
        hook = proj / ".githooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\n"
            "# 团队共享的提交检查\n"
            'root="$(git rev-parse --show-toplevel)"\n'
            "exec python3 -c \"print('host-gate-ok')\"\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        self.run_cmd(["git", "add", ".githooks/pre-commit"], cwd=proj)
        self.run_cmd(["git", "commit", "-m", "team hook"], cwd=proj)

        res = self.run_cmd(["python3", str(WS_BIN), "init", str(proj)])
        self.assertEqual(res.returncode, 0, res.stderr)

        # 1) 门禁片段排在宿主逻辑之前（追加在 exec 之后永远执行不到）；已入库的 .githooks/ 不被写进忽略清单
        injected = hook.read_text(encoding="utf-8")
        self.assertTrue(injected.startswith("#!/bin/sh\n# ws-harness-guard:start\n"), injected)
        self.assertLess(injected.index("ws-harness-guard:end"), injected.index("exec python3"))
        self.assertIn("# 团队共享的提交检查", injected)
        self.assertNotIn(".githooks/", (proj / ".gitignore").read_text(encoding="utf-8").splitlines())

        # 2) design 模式下暂存业务代码：门禁真的拦住；放行时宿主钩子照常接管
        (proj / "backend").mkdir()
        (proj / "backend" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "backend/app.py"], cwd=proj)
        blocked = self.run_cmd(["sh", ".githooks/pre-commit"], cwd=proj)
        self.assertNotEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertIn("backend/app.py", blocked.stderr)
        self.assertNotIn("host-gate-ok", blocked.stdout)
        self.run_cmd(["git", "rm", "--cached", "-q", "backend/app.py"], cwd=proj)
        passed = self.run_cmd(["sh", ".githooks/pre-commit"], cwd=proj)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("host-gate-ok", passed.stdout)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "doctor", str(proj)]).returncode, 0)

        # 3) 旧版把片段追加在末尾（exec 之后）的钩子：doctor 必须报“执行不到”，ws link 把它挪回开头且重复执行不再改动
        block = re.search(r"# ws-harness-guard:start.*?# ws-harness-guard:end\n", injected, re.DOTALL).group(0)
        hook.write_text(injected.replace(block, "").rstrip() + "\n\n" + block, encoding="utf-8")
        stale = self.run_cmd(["python3", str(WS_BIN), "doctor", str(proj)])
        self.assertEqual(stale.returncode, 1, stale.stdout)
        self.assertIn("执行不到", stale.stdout)

        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "link", str(proj)]).returncode, 0)
        self.assertEqual(hook.read_text(encoding="utf-8"), injected)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "link", str(proj)]).returncode, 0)
        self.assertEqual(hook.read_text(encoding="utf-8"), injected)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "doctor", str(proj)]).returncode, 0)

    def test_13_parallel_worktrees_get_local_skills_and_hooks(self):
        proj = self.tmp_path / "main-app"
        proj.mkdir()
        self.run_cmd(["git", "init", "-b", "main"], cwd=proj)
        (proj / "README.md").write_text("# main-app\n", encoding="utf-8")
        self.run_cmd(["git", "add", "README.md"], cwd=proj)
        self.run_cmd(["git", "commit", "-m", "接入工作流之前的提交"], cwd=proj)

        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj)]).returncode, 0)
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        adopt = self.run_cmd(["git", "commit", "-m", "接入工作流"], cwd=proj)
        self.assertEqual(adopt.returncode, 0, adopt.stdout + adopt.stderr)
        # 技能软链接没有随提交入库
        tracked = self.run_cmd(["git", "ls-files"], cwd=proj).stdout
        self.assertNotIn(".agents/skills/explore-architect", tracked)

        # 并行开发线放在比主目录深一层的位置（入库的相对软链接在这里一定会断）；另开一个停在旧提交的验收检出
        line = self.tmp_path / "lines" / "feature"
        frozen = self.tmp_path / "lines" / "frozen"
        self.assertEqual(self.run_cmd(["git", "worktree", "add", str(line), "-b", "feature"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["git", "worktree", "add", "--detach", str(frozen), "HEAD~1"], cwd=proj).returncode, 0)

        # 1) 新开的工作树里还没有技能与钩子：doctor 如实报缺并提示 ws link
        before = self.run_cmd(["python3", str(WS_BIN), "doctor", str(line)])
        self.assertEqual(before.returncode, 1, before.stdout)
        self.assertIn("ws link", before.stdout)

        # 2) 无参 ws link 连同并行工作树一起补齐；不动停在旧提交（无宪章）的检出
        link_res = self.run_cmd(["python3", str(WS_BIN), "link"], cwd=HUB)
        self.assertEqual(link_res.returncode, 0, link_res.stderr)
        self.assertIn("并行工作树 1 条", link_res.stdout)
        for skill in (
            "explore-architect",
            "bespoke-prototype",
            "tech-architect",
            "slice-build",
            "systematic-debugging",
            "adversarial-review",
            "compound-learn",
        ):
            self.assertTrue((line / ".agents" / "skills" / skill / "SKILL.md").is_file(), f"{skill} 在工作树里应可读")
        self.assertTrue((line / ".claude" / "settings.local.json").is_file())
        self.assertEqual(self.run_cmd(["git", "status", "--porcelain"], cwd=line).stdout, "")
        self.assertFalse((frozen / ".agents").exists())
        self.assertEqual(self.run_cmd(["git", "status", "--porcelain"], cwd=frozen).stdout, "")

        # 3) 工作树里真实执行一次 git commit：design 模式下提交业务代码被门禁拦下
        (line / "src").mkdir()
        (line / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "src/app.py"], cwd=line)
        commit = self.run_cmd(["git", "commit", "-m", "抢跑"], cwd=line)
        self.assertNotEqual(commit.returncode, 0, commit.stdout)
        self.assertIn("Mode: design", commit.stderr)
        self.run_cmd(["git", "rm", "--cached", "-q", "src/app.py"], cwd=line)
        (line / "src" / "app.py").unlink()

        after = self.run_cmd(["python3", str(WS_BIN), "doctor", str(line)])
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)

    def test_14_ws_map_and_read_guard(self):
        proj = self.tmp_path / "map-guard-app"
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--mode", "build"]).returncode, 0)

        src = proj / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "AGENTS.md").write_text("# 核心计算模块\n\n职责：净值计算与组件渲染。\n", encoding="utf-8")

        engine_lines = [
            '"""净值计算引擎：负责多币种持仓折算与风险敞口汇总。"""',
            "from __future__ import annotations",
            "",
            "class PortfolioEngine:",
            "    def __init__(self, base_ccy: str = 'USD') -> None:",
            "        self.base_ccy = base_ccy",
            "",
            "    def total_exposure(self, positions: list[float]) -> float:",
            "        return sum(positions)",
            "",
            "async def compute_nav(nav_id: str, prices: dict[str, float]) -> float:",
            "    total = sum(prices.values())",
        ]
        for idx in range(1, 450):
            engine_lines.append(f"    total += {idx} * 0.0")
        engine_lines.append("    return total")
        engine_file = src / "engine.py"
        engine_file.write_text("\n".join(engine_lines) + "\n", encoding="utf-8")
        self.assertGreater(len(engine_lines), 450)

        widget_file = src / "widget.tsx"
        widget_file.write_text(
            "// 持仓摘要卡片组件\n"
            "export interface WidgetProps {\n"
            "  title: string;\n"
            "  nav: number;\n"
            "}\n\n"
            "export function WidgetCard(props: WidgetProps) {\n"
            "  return <div>{props.title}: {props.nav}</div>;\n"
            "}\n",
            encoding="utf-8",
        )

        gen_dir = src / "generated"
        gen_dir.mkdir()
        (gen_dir / "client.gen.ts").write_text(
            "// Auto-generated client\nexport const apiVersion = () => 'v1';\n",
            encoding="utf-8",
        )

        docs_dir = proj / "docs"
        docs_dir.mkdir(exist_ok=True)
        md_lines = [
            "# 架构指南",
            "",
            "## 第一章：总览",
            "```python",
            "# 代码块里的注释不应被识别为章节标题",
            "def demo(): pass",
            "```",
            "## 第二章：存储约定",
        ] + [f"正文说明第 {i} 行" for i in range(430)]
        guide_file = docs_dir / "guide.md"
        guide_file.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        # 1) ws map 目录模式：AGENTS.md 与手写代码排在自动生成目录之前，且包含 AST 签名与行号
        map_res = self.run_cmd(["python3", str(WS_BIN), "map", str(src)])
        self.assertEqual(map_res.returncode, 0, map_res.stderr)
        out = map_res.stdout
        self.assertIn("class PortfolioEngine", out)
        self.assertIn("async def compute_nav(nav_id: str, prices: dict[str, float]) -> float", out)
        self.assertIn("export interface WidgetProps", out)
        self.assertIn("export function WidgetCard", out)
        self.assertLess(out.index("AGENTS.md"), out.index("engine.py"))
        self.assertLess(out.index("widget.tsx"), out.index("generated/client.gen.ts"))

        # 2) 无 ast-grep 降级扫描仍能提取 TS 符号
        no_sg_env = {**self.env, "WS_DISABLE_AST_GREP": "1"}
        fallback_res = subprocess.run(
            ["python3", str(WS_BIN), "map", str(widget_file)],
            text=True,
            capture_output=True,
            env=no_sg_env,
        )
        self.assertEqual(fallback_res.returncode, 0, fallback_res.stderr)
        self.assertIn("WidgetProps", fallback_res.stdout)
        self.assertIn("WidgetCard", fallback_res.stdout)

        # 3) Markdown 围栏内 `#` 不误判为标题
        md_map = self.run_cmd(["python3", str(WS_BIN), "map", str(guide_file)])
        self.assertEqual(md_map.returncode, 0)
        self.assertIn("## 第二章：存储约定", md_map.stdout)
        self.assertNotIn("代码块里的注释不应被识别为章节标题", md_map.stdout)

        # 4) read_guard：小文件无感放行（AGY + Claude）
        agy_small = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "read_file", "args": {"Path": str(widget_file)}},
            }),
        )
        self.assertEqual(json.loads(agy_small.stdout)["decision"], "allow")

        cc_small = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "tool_name": "Read",
                "tool_input": {"file_path": str(widget_file)},
            }),
        )
        self.assertEqual(json.loads(cc_small.stdout), {})

        # 5) read_guard：大文件无行号整本读取拦截，并当场返回实时 AST 骨架与行号
        agy_big = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "read_file", "args": {"Path": str(engine_file)}},
            }),
        )
        agy_big_json = json.loads(agy_big.stdout)
        self.assertEqual(agy_big_json["decision"], "deny")
        self.assertIn("class PortfolioEngine", agy_big_json["reason"])
        self.assertIn("StartLine", agy_big_json["reason"])

        cc_big = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "tool_name": "Read",
                "tool_input": {"file_path": str(engine_file)},
            }),
        )
        cc_big_json = json.loads(cc_big.stdout)
        self.assertEqual(cc_big_json["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("offset", cc_big_json["hookSpecificOutput"]["permissionDecisionReason"])

        # 6) read_guard：大文件伪造超大跨度（如 1..450）同样拦截；合理按段精读（如 1..80）放行
        agy_overspan = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "read_file", "args": {"Path": str(engine_file), "StartLine": 1, "EndLine": 450}},
            }),
        )
        self.assertEqual(json.loads(agy_overspan.stdout)["decision"], "deny")

        agy_ranged = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "read_file", "args": {"Path": str(engine_file), "StartLine": 1, "EndLine": 80}},
            }),
        )
        self.assertEqual(json.loads(agy_ranged.stdout)["decision"], "allow")

        cc_ranged = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "tool_name": "Read",
                "tool_input": {"file_path": str(engine_file), "offset": 1, "limit": 80},
            }),
        )
        self.assertEqual(json.loads(cc_ranged.stdout), {})

        # 7) 仓库外纯临时探针文件（/tmp 或系统临时目录）与 SKILL.md 豁免直读
        scratch_probe = self.tmp_path / "raw_probe_output.log"
        scratch_probe.write_text("\n".join(f"line {i}" for i in range(500)) + "\n", encoding="utf-8")
        agy_scratch = self.run_cmd(
            ["python3", str(READ_GUARD)],
            stdin_text=json.dumps({
                "cwd": str(proj),
                "toolCall": {"name": "read_file", "args": {"Path": str(scratch_probe)}},
            }),
        )
        self.assertEqual(json.loads(agy_scratch.stdout)["decision"], "allow")

        # 8) 误传命令行参数（如 --map）且 stdin 保持打开未发 EOF 时，必须毫秒级报错退出，绝不挂起阻塞后台任务
        proc = subprocess.Popen(
            ["python3", str(READ_GUARD), "--map", str(engine_file)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
        )
        try:
            rc = proc.wait(timeout=2)
            err = proc.stderr.read() if proc.stderr else ""
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream and not stream.closed:
                    stream.close()
        self.assertEqual(rc, 2)
        self.assertIn("ws map", err)

    def test_15_worktree_local_mode_state_isolation(self):
        proj = self.tmp_path / "multi-line-app"
        proj.mkdir()
        self.run_cmd(["git", "init", "-b", "main"], cwd=proj)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj)]).returncode, 0)
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "init design main"], cwd=proj).returncode, 0)

        wt = self.tmp_path / "lines" / "pb-quant"
        self.assertEqual(self.run_cmd(["git", "worktree", "add", str(wt), "-b", "line/pb-quant"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "link", str(proj)]).returncode, 0)

        # 1) 默认继承 AGENTS.md 的 Mode: design；直接篡改 ws-mode-state.json 必须被 deny，执行 ws mode build --local 触发 ask
        self.assertEqual(
            self.claude_decision(wt, "Write", {"file_path": str(proj / ".git" / "worktrees" / "pb-quant" / "ws-mode-state.json"), "content": '{"mode":"build"}'}),
            "deny",
        )
        self.assertEqual(
            self.claude_decision(wt, "Bash", {"command": "echo '{\"mode\":\"build\"}' > .git/ws-mode-state.json"}),
            "deny",
        )
        self.assertEqual(
            self.claude_decision(wt, "Bash", {"command": f"python3 {WS_BIN} mode build --local --slice design/slices/01-quant.md"}),
            "ask",
        )
        self.assertEqual(
            self.claude_decision(wt, "Bash", {"command": f"python3 {WS_BIN} mode --local build"}),
            "ask",
        )

        # 2) 用户确认运行 ws mode build --local：工作树解锁 build 且 AGENTS.md 零改动，主仓仍保持 Mode: design 严防误改
        unlock = self.run_cmd(
            ["python3", str(WS_BIN), "mode", "build", str(wt), "--local", "--slice", "design/slices/01-quant.md"]
        )
        self.assertEqual(unlock.returncode, 0, unlock.stderr)
        self.assertEqual(self.run_cmd(["git", "status", "--porcelain"], cwd=wt).stdout, "")
        self.assertIn("- **Mode**: `design`", (wt / "AGENTS.md").read_text(encoding="utf-8"))

        # 工作树内写业务代码并提交畅通无阻
        self.assertEqual(self.claude_decision(wt, "Write", {"file_path": str(wt / "src" / "quant.py"), "content": "x = 1\n"}), "allow")
        (wt / "src").mkdir()
        (wt / "src" / "quant.py").write_text("x = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "src/quant.py"], cwd=wt)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: quant"], cwd=wt).returncode, 0)

        # 主仓仍锁在 Mode: design（即使从工作树 cwd 跨目录写主仓源码也照样拦截）
        self.assertEqual(
            self.claude_decision(wt, "Write", {"file_path": str(proj / "src" / "quant.py"), "content": "x = 1\n"}),
            "deny",
        )

        # 3) ws doctor 透明展示本地 Mode 覆盖；--clear-local 恢复继承 AGENTS.md 的 design 门禁
        doc = self.run_cmd(["python3", str(WS_BIN), "doctor", str(wt)])
        self.assertEqual(doc.returncode, 0, doc.stdout + doc.stderr)
        self.assertIn("本地 Mode 覆盖: build", doc.stdout)

        clr = self.run_cmd(["python3", str(WS_BIN), "mode", "--clear-local", str(wt)])
        self.assertEqual(clr.returncode, 0, clr.stderr)
        self.assertEqual(self.claude_decision(wt, "Write", {"file_path": str(wt / "src" / "quant.py"), "content": "x = 2\n"}), "deny")

        # 4) 常规（非 --local）ws mode 切换 AGENTS.md 时自动清理残留本地状态，防止旧本地覆盖遮蔽新模式
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "build", str(wt), "--local"]).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "design", str(wt)]).returncode, 0)
        self.assertEqual(self.claude_decision(wt, "Write", {"file_path": str(wt / "src" / "quant.py"), "content": "x = 3\n"}), "deny")

    def test_16_ws_line_parallel_lifecycle_and_gates(self):
        proj = self.tmp_path / "crypto-app"
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj)]).returncode, 0)

        # 1) 尚无初始提交（No commits yet）时，ws line new 给出清晰引导并拒绝创建
        no_commit = self.run_cmd(["python3", str(WS_BIN), "line", "new", "01a-overlays", str(proj)])
        self.assertNotEqual(no_commit.returncode, 0)
        self.assertIn("尚无初始提交", no_commit.stderr)

        # 2) Mode: design 下，AI 试图调 ws line new（默认解锁 build）必须被 mode_guard 升级为 ask
        self.assertEqual(
            self.claude_decision(proj, "Bash", {"command": f"python3 {WS_BIN} line new 01a-overlays"}),
            "ask",
        )
        self.assertEqual(
            self.claude_decision(proj, "Bash", {"command": f"python3 {WS_BIN} line new 01a-overlays --mode design"}),
            "allow",
        )
        self.assertEqual(
            self.claude_decision(proj, "Bash", {"command": f"python3 {WS_BIN} line list"}),
            "allow",
        )

        # 3) 模拟完成首片基线提交（含可执行 scripts/check 及本地忽略的 .env）后切回 Mode: design 守门
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "build", str(proj)]).returncode, 0)
        (proj / "scripts").mkdir(parents=True, exist_ok=True)
        chk_script = proj / "scripts" / "check"
        chk_script.write_text(
            "#!/bin/sh\n"
            "set -e\n"
            'ROOT="$(cd "$(dirname "$0")/.." && pwd)"\n'
            'if [ -f "$ROOT/FAIL_GATE" ]; then echo "custom gate failed" >&2; exit 1; fi\n'
            f'python3 "{WS_BIN}" check "$ROOT"\n',
            encoding="utf-8",
        )
        chk_script.chmod(0o755)
        gi = proj / ".gitignore"
        gi.write_text(gi.read_text(encoding="utf-8") + "repro/\n", encoding="utf-8")
        (proj / ".env").write_text("OKX_PROXY=http://127.0.0.1:7897\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: slice 00 foundation"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "design", str(proj)]).returncode, 0)
        self.run_cmd(["git", "add", "AGENTS.md"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "chore: lock main in design mode"], cwd=proj).returncode, 0)

        # 4) 开工前多方案比选门禁：缺失切片单、proposed/draft 未拍板状态、或省略 --slice 绕过均必须硬拦截
        missing_slice = self.run_cmd([
            "python3", str(WS_BIN), "line", "new", "01a-overlays", str(proj),
            "--slice", "design/slices/01a-overlays.md", "--scope", "src/overlays",
        ])
        self.assertNotEqual(missing_slice.returncode, 0)
        self.assertIn("尚不存在", missing_slice.stderr)

        slice_a = proj / "design" / "slices" / "01a-overlays.md"
        slice_a.write_text("# Slice 01a\n\n- **状态**：`proposed`\n", encoding="utf-8")
        unapproved_slice = self.run_cmd([
            "python3", str(WS_BIN), "line", "new", "01a-overlays", str(proj),
            "--slice", "design/slices/01a-overlays.md", "--scope", "src/overlays",
        ])
        self.assertNotEqual(unapproved_slice.returncode, 0)
        self.assertIn("尚未经用户从多方案比选中拍板选定", unapproved_slice.stderr)

        sneak_no_slice = self.run_cmd([
            "python3", str(WS_BIN), "line", "new", "01a-overlays", str(proj), "--scope", "src/overlays",
        ])
        self.assertNotEqual(sneak_no_slice.returncode, 0)
        self.assertIn("禁止省略 `--slice` 绕过", sneak_no_slice.stderr)

        # 用户选定方案后更新为 approved（主仓未入库新切片单自动迁入工作树，保持主仓 100% 干净免合流冲突）
        slice_a.write_text("# Slice 01a\n\n- **状态**：`approved`\n- **选定方案**：方案 A\n", encoding="utf-8")
        l1 = self.run_cmd([
            "python3", str(WS_BIN), "line", "new", "01a-overlays", str(proj),
            "--slice", "design/slices/01a-overlays.md", "--scope", "src/overlays",
        ])
        self.assertEqual(l1.returncode, 0, l1.stderr)
        wt_a = self.tmp_path / "crypto-app-lines" / "01a-overlays"
        self.assertTrue(wt_a.is_dir())
        self.assertTrue((wt_a / "design" / "slices" / "01a-overlays.md").is_file())
        self.assertFalse(slice_a.exists(), "未入库切片单应迁入开发线工作树以免日后快进合流冲突")
        self.assertEqual(self.run_cmd(["git", "status", "--porcelain"], cwd=proj).stdout, "")
        self.assertTrue((wt_a / ".env").is_symlink(), ".env 应自动软链至并行工作树")
        self.assertEqual((wt_a / ".env").read_text(encoding="utf-8"), "OKX_PROXY=http://127.0.0.1:7897\n")
        self.assertEqual(
            self.claude_decision(wt_a, "Write", {"file_path": str(wt_a / "src" / "overlays" / "ema.py"), "content": "v = 1\n"}),
            "allow",
        )
        self.assertEqual(
            self.claude_decision(proj, "Write", {"file_path": str(proj / "src" / "overlays" / "ema.py"), "content": "v = 1\n"}),
            "deny",
        )

        # 同范围重复开线默认阻断
        dup_scope = self.run_cmd([
            "python3", str(WS_BIN), "line", "new", "01a-sub", str(proj), "--scope", "src/overlays/vwap",
        ])
        self.assertNotEqual(dup_scope.returncode, 0)
        self.assertIn("范围冲突", dup_scope.stderr)

        # 正交范围正常开第二条线
        slice_b = proj / "design" / "slices" / "01b-exec.md"
        slice_b.write_text("# Slice 01b\n\n- **状态**：`approved`\n- **选定方案**：方案 B\n", encoding="utf-8")
        l2 = self.run_cmd([
            "python3", str(WS_BIN), "line", "new", "01b-exec", str(proj),
            "--slice", "design/slices/01b-exec.md", "--scope", "src/exec",
        ])
        self.assertEqual(l2.returncode, 0, l2.stderr)
        wt_b = self.tmp_path / "crypto-app-lines" / "01b-exec"
        self.assertTrue((wt_b / "design" / "slices" / "01b-exec.md").is_file())
        self.assertEqual(self.run_cmd(["git", "status", "--porcelain"], cwd=proj).stdout, "")

        ls_out = self.run_cmd(["python3", str(WS_BIN), "line", "list", str(proj)])
        self.assertEqual(ls_out.returncode, 0)
        self.assertIn("01a-overlays", ls_out.stdout)
        self.assertIn("01b-exec", ls_out.stdout)

        # 5) 两条线分别改各自模块：ws line check 通过；若同时触碰同一文件则立即检出冲突
        (wt_a / "src" / "overlays").mkdir(parents=True)
        (wt_a / "src" / "overlays" / "ema.py").write_text("def calc_ema():\n    return 20\n", encoding="utf-8")
        (wt_b / "src" / "exec").mkdir(parents=True)
        (wt_b / "src" / "exec" / "order.py").write_text("def place_order():\n    return True\n", encoding="utf-8")
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "check", str(proj)]).returncode, 0)

        (wt_a / "src" / "shared.py").write_text("A = 1\n", encoding="utf-8")
        (wt_b / "src" / "shared.py").write_text("B = 2\n", encoding="utf-8")
        collide = self.run_cmd(["python3", str(WS_BIN), "line", "check", str(proj)])
        self.assertNotEqual(collide.returncode, 0)
        self.assertIn("src/shared.py", collide.stderr)
        (wt_a / "src" / "shared.py").unlink()
        (wt_b / "src" / "shared.py").unlink()

        # 6) 线 A 提交后验证门禁阻断、合流与防误删保护
        (wt_a / "FAIL_GATE").write_text("boom\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt_a)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: overlays wip"], cwd=wt_a).returncode, 0)

        # 未合入前拒绝 ws line rm
        rm_early = self.run_cmd(["python3", str(WS_BIN), "line", "rm", "01a-overlays", str(proj)])
        self.assertNotEqual(rm_early.returncode, 0)
        self.assertIn("未合入", rm_early.stderr)

        # 门禁失败时阻断 ws line sync 合入 main
        sync_fail = self.run_cmd(["python3", str(WS_BIN), "line", "sync"], cwd=wt_a)
        self.assertNotEqual(sync_fail.returncode, 0)
        self.assertFalse((proj / "src" / "overlays" / "ema.py").exists())

        # 修复门禁后，未登记验收沉淀前仍阻断合入 main；完成 ws line distill 后合入通过
        self.run_cmd(["git", "rm", "-q", "FAIL_GATE"], cwd=wt_a)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "fix: remove fail marker"], cwd=wt_a).returncode, 0)
        sync_no_distill = self.run_cmd(["python3", str(WS_BIN), "line", "sync"], cwd=wt_a)
        self.assertNotEqual(sync_no_distill.returncode, 0)
        self.assertIn("尚未登记真人验收后的收尾沉淀", sync_no_distill.stderr)
        self.assertFalse((proj / "src" / "overlays" / "ema.py").exists())

        dist_a = self.run_cmd(["python3", str(WS_BIN), "line", "distill", "--note", "验收通过并核对契约"], cwd=wt_a)
        self.assertEqual(dist_a.returncode, 0, dist_a.stdout + dist_a.stderr)
        sync_a = self.run_cmd(["python3", str(WS_BIN), "line", "sync"], cwd=wt_a)
        self.assertEqual(sync_a.returncode, 0, sync_a.stdout + sync_a.stderr)
        self.assertTrue((proj / "src" / "overlays" / "ema.py").is_file())

        # 若工作树残留未入库的本地独有数据文件（非缓存、非软链），rm 默认拦截防误删
        (wt_a / "repro").mkdir()
        (wt_a / "repro" / "sample.json").write_text("{}\n", encoding="utf-8")
        rm_precious = self.run_cmd(["python3", str(WS_BIN), "line", "rm", "01a-overlays", str(proj)])
        self.assertNotEqual(rm_precious.returncode, 0)
        self.assertIn("repro/", rm_precious.stderr)
        (wt_a / "repro" / "sample.json").unlink()
        (wt_a / "repro").rmdir()

        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "rm", "01a-overlays", str(proj)]).returncode, 0)
        self.assertFalse(wt_a.exists())

        # 7) 线 B 此时落后于已推进的 main：先 distill 锁当前 HEAD，ws line sync 自动 rebase 跟上 main 过检并同步推进 distilled_head
        self.run_cmd(["git", "add", "-A"], cwd=wt_b)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: order execution"], cwd=wt_b).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "distill", "01b-exec", str(proj)]).returncode, 0)
        sync_b = self.run_cmd(["python3", str(WS_BIN), "line", "sync", "01b-exec", str(proj)])
        self.assertEqual(sync_b.returncode, 0, sync_b.stdout + sync_b.stderr)
        self.assertTrue((proj / "src" / "exec" / "order.py").is_file())
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "rm", "01b-exec", str(proj)]).returncode, 0)
        self.assertFalse((self.tmp_path / "crypto-app-lines").exists(), "最后一条线收掉后空父目录应自动清理")

    def test_17_ws_line_adversarial_security_and_edge_cases(self):
        proj = self.tmp_path / "adv-app"
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--mode", "build"]).returncode, 0)
        (proj / "api").mkdir()
        (proj / "api" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
        (proj / "scripts").mkdir(exist_ok=True)
        chk = proj / "scripts" / "check"
        chk.write_text(
            "#!/bin/sh\n"
            "set -e\n"
            'ROOT="$(cd "$(dirname "$0")/.." && pwd)"\n'
            f'python3 "{WS_BIN}" check "$ROOT" --diff\n',
            encoding="utf-8",
        )
        chk.chmod(0o755)
        gi = proj / ".gitignore"
        gi.write_text(gi.read_text(encoding="utf-8") + ".codex/\n", encoding="utf-8")
        (proj / ".codex").mkdir()
        (proj / ".codex" / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: base"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "design", str(proj)]).returncode, 0)
        self.run_cmd(["git", "add", "AGENTS.md"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "chore: lock design"], cwd=proj).returncode, 0)

        # 1) B1 对抗绕过：引号包裹、参数后置覆盖、前缀缩写、注释伪装、自定义 scripts/line new 均须触发 ask；--help 放行
        for bypass_cmd in (
            f'python3 "{WS_BIN}" "line" "new" sneak',
            f'"{WS_BIN}" line new sneak --mode design --mode build',
            f'"{WS_BIN}" line new sneak --mode=design --mo=build',
            f'"{WS_BIN}" line new sneak # --mode design',
            f'"{WS_BIN}" line new sneak --slice design/slices/01--mode=design.md',
            "./scripts/line new sneak",
            f'"{WS_BIN}" line sync sneak --skip-check',
            f'"{WS_BIN}" line sync sneak --skip-distill',
        ):
            self.assertEqual(
                self.claude_decision(proj, "Bash", {"command": bypass_cmd}),
                "ask",
                f"命令未触发 ask: {bypass_cmd}",
            )
        self.assertEqual(
            self.claude_decision(proj, "Bash", {"command": f'"{WS_BIN}" line new --help'}),
            "allow",
        )
        # argparse allow_abbrev=False 拒绝缩写参数
        abbrev = self.run_cmd(["python3", str(WS_BIN), "line", "new", "sneak", str(proj), "--mo=design"])
        self.assertNotEqual(abbrev.returncode, 0)

        # 2) 开启与主仓子目录同名的开发线 `api`（含 .codex/config.toml 软链继承）
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "new", "api", str(proj)]).returncode, 0)
        wt_api = self.tmp_path / "adv-app-lines" / "api"
        self.assertTrue((wt_api / ".codex" / "config.toml").is_symlink())

        # 从已获批的 build 子工作树跨目录触碰 design 主仓：开新线/解锁主仓须 ask，直接 shell 写主仓源码须 deny
        self.assertEqual(
            self.claude_decision(wt_api, "Bash", {"command": f'python3 "{WS_BIN}" line new sneak2'}),
            "ask",
        )
        self.assertEqual(
            self.claude_decision(wt_api, "Bash", {"command": f'python3 "{WS_BIN}" mode build "{proj}"'}),
            "ask",
        )
        self.assertEqual(
            self.claude_decision(wt_api, "Bash", {"command": f'echo hacked > "{proj}/api/base.py"'}),
            "deny",
        )

        # 3) B2 合流范围扫描：即使工作树干净，分支提交含新增 TODO（--diff 模式）或历史提交曾混入密钥再删除，sync 均须拦截
        (wt_api / "api" / "todo_mod.py").write_text("# TODO: unfinished logic\nx = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt_api)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "wip todo"], cwd=wt_api).returncode, 0)
        # 在主仓目录运行 `ws line sync api`（主仓存在同名子目录 api/）应准确命中开发线并被 --diff 门禁拦下
        sync_todo = self.run_cmd(["python3", str(WS_BIN), "line", "sync", "api"], cwd=proj)
        self.assertNotEqual(sync_todo.returncode, 0)

        # 修掉 TODO，但模拟用 --no-verify 先提交真格式密钥、下一提交再删掉（历史残留密钥）
        (wt_api / "api" / "todo_mod.py").write_text("x = 1\n", encoding="utf-8")
        fake_sk = "sk-" + "a" * 32
        (wt_api / "api" / "leak.py").write_text(f'API_KEY = "{fake_sk}"\n', encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt_api)
        self.assertEqual(self.run_cmd(["git", "commit", "--no-verify", "-m", "oops leak"], cwd=wt_api).returncode, 0)
        self.run_cmd(["git", "rm", "-q", "api/leak.py"], cwd=wt_api)
        self.assertEqual(self.run_cmd(["git", "commit", "--no-verify", "-m", "delete leak file"], cwd=wt_api).returncode, 0)

        # 即使传 --skip-check，历史提交密钥扫描依然硬拦截
        sync_leak = self.run_cmd(["python3", str(WS_BIN), "line", "sync", "api", "--skip-check"], cwd=proj)
        self.assertNotEqual(sync_leak.returncode, 0)
        self.assertIn("密钥", sync_leak.stderr)

        # 重置回干净基线并提交合法代码后合入；验证含 .codex/config.toml 软链时 rm 不会假阳性阻断，而实体 .env 会受保护
        base_sha = self.run_cmd(["git", "rev-parse", "main"], cwd=proj).stdout.strip()
        self.run_cmd(["git", "reset", "--hard", base_sha], cwd=wt_api)
        (wt_api / "api" / "clean.py").write_text("READY = True\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt_api)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: clean api"], cwd=wt_api).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "distill", "api"], cwd=proj).returncode, 0)
        sync_ok = self.run_cmd(["python3", str(WS_BIN), "line", "sync", "api"], cwd=proj)
        self.assertEqual(sync_ok.returncode, 0, sync_ok.stdout + sync_ok.stderr)

        (wt_api / ".env").write_text("LOCAL_CUSTOM_SECRET=1\n", encoding="utf-8")
        rm_env_block = self.run_cmd(["python3", str(WS_BIN), "line", "rm", "api", str(proj)])
        self.assertNotEqual(rm_env_block.returncode, 0)
        self.assertIn(".env", rm_env_block.stderr)
        (wt_api / ".env").unlink()

        rm_ok = self.run_cmd(["python3", str(WS_BIN), "line", "rm", "api", str(proj)])
        self.assertEqual(rm_ok.returncode, 0, rm_ok.stderr)

        # 4) B3/W6 变基冲突中断（detached HEAD）时 check / sync / rm 全链路保护
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "new", "conflict-line", str(proj)]).returncode, 0)
        wt_cf = self.tmp_path / "adv-app-lines" / "conflict-line"
        (wt_cf / "api" / "base.py").write_text("BASE = 999\n", encoding="utf-8")
        self.run_cmd(["git", "add", "api/base.py"], cwd=wt_cf)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: line change base"], cwd=wt_cf).returncode, 0)

        # 推进 main 制造同文件同位置冲突
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "build", str(proj)]).returncode, 0)
        (proj / "api" / "base.py").write_text("BASE = 2\n", encoding="utf-8")
        self.run_cmd(["git", "add", "api/base.py"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: main change base"], cwd=proj).returncode, 0)

        # 变基前 ws line check 预演即检出冲突
        pre_chk = self.run_cmd(["python3", str(WS_BIN), "line", "check", str(proj)])
        self.assertNotEqual(pre_chk.returncode, 0)
        self.assertIn("预合并存在代码冲突", pre_chk.stderr)

        # sync 触发 rebase 冲突停在半途：再次 check / sync / rm（无 --discard）全部安全阻断
        rb_fail = self.run_cmd(["python3", str(WS_BIN), "line", "sync"], cwd=wt_cf)
        self.assertNotEqual(rb_fail.returncode, 0)
        self.assertNotEqual(self.run_cmd(["python3", str(WS_BIN), "line", "check", str(proj)]).returncode, 0)
        self.assertNotEqual(self.run_cmd(["python3", str(WS_BIN), "line", "sync"], cwd=wt_cf).returncode, 0)
        self.assertNotEqual(self.run_cmd(["python3", str(WS_BIN), "line", "rm", "conflict-line", str(proj)]).returncode, 0)
        # 显式 --discard 可强制回收冲突废弃线
        self.assertEqual(
            self.run_cmd(["python3", str(WS_BIN), "line", "rm", "conflict-line", str(proj), "--discard"]).returncode,
            0,
        )

        # 5) B4 项目自带可执行 scripts/line 时，ws line 主动让路防破坏项目专属状态
        custom_line = proj / "scripts" / "line"
        custom_line.write_text("#!/bin/sh\necho custom line\n", encoding="utf-8")
        custom_line.chmod(0o755)
        delegated = self.run_cmd(["python3", str(WS_BIN), "line", "new", "x", str(proj)])
        self.assertNotEqual(delegated.returncode, 0)
        self.assertIn("scripts/line", delegated.stderr)

    def test_18_full_check_receipt_across_worktrees_and_doc_commits(self):
        proj = self.tmp_path / "ci-quota-app"
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--mode", "build"]).returncode, 0)
        (proj / "src" / "prompts").mkdir(parents=True)
        (proj / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (proj / "src" / "prompts" / "ask.md").write_text("System prompt v1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "init baseline"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "design", str(proj)]).returncode, 0)
        self.run_cmd(["git", "add", "AGENTS.md"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "lock design"], cwd=proj).returncode, 0)

        # 1) 未签发全量凭证前，--verify-full 与 --full-covered 必须拦截；互斥参数必须拦截
        unverified = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--verify-full"])
        self.assertNotEqual(unverified.returncode, 0)
        self.assertIn("尚未在本机签发全量门禁凭证", unverified.stderr)

        uncovered = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--full-covered"])
        self.assertNotEqual(uncovered.returncode, 0)

        diff_conflict = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--diff", "--record-full"])
        self.assertNotEqual(diff_conflict.returncode, 0)
        cov_conflict = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--record-full", "--full-covered"])
        self.assertNotEqual(cov_conflict.returncode, 0)

        # 2) 在并行开发线中修改代码、签发全量凭证、合入主仓、收掉开发线，并在主仓追加纯文档/设计提交：凭证依然有效
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "new", "feat-ci", str(proj)]).returncode, 0)
        wt = self.tmp_path / "ci-quota-app-lines" / "feat-ci"
        (wt / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        rec_ok = self.run_cmd(["python3", str(WS_BIN), "check", str(wt), "--record-full"])
        self.assertEqual(rec_ok.returncode, 0, rec_ok.stdout + rec_ok.stderr)
        wt_cov = self.run_cmd(["python3", str(WS_BIN), "check", str(wt), "--full-covered"])
        self.assertEqual(wt_cov.returncode, 0, wt_cov.stdout + wt_cov.stderr)
        self.run_cmd(["git", "add", "-A"], cwd=wt)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: value 2"], cwd=wt).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "distill", "feat-ci"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "sync", "feat-ci"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "rm", "feat-ci", str(proj)]).returncode, 0)

        (proj / "docs").mkdir(exist_ok=True)
        (proj / "docs" / "progress.md").write_text("# 进度沉淀\n", encoding="utf-8")
        (proj / "design" / "SYSTEM.md").write_text("# 骨架\n", encoding="utf-8")
        (proj / "README.md").write_text("# 说明\n", encoding="utf-8")
        # 未提交的纯文档改动不影响 --full-covered 命中
        doc_dirty_cov = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--full-covered"])
        self.assertEqual(doc_dirty_cov.returncode, 0, doc_dirty_cov.stdout + doc_dirty_cov.stderr)
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "docs: distill"], cwd=proj).returncode, 0)
        self.run_cmd(["git", "tag", "-a", "v1.0.0", "-m", "v1.0.0"], cwd=proj)

        ver_ok = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--verify-full", "HEAD"])
        self.assertEqual(ver_ok.returncode, 0, ver_ok.stdout + ver_ok.stderr)
        main_cov = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--full-covered"])
        self.assertEqual(main_cov.returncode, 0, main_cov.stdout + main_cov.stderr)

        # 3) 源码树内的业务 prompt/*.md 属于运行时代码而非纯文档：修改后未重签凭证必须拦截，而历史标签 v1.0.0 仍有效
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "build", str(proj)]).returncode, 0)
        (proj / "src" / "prompts" / "ask.md").write_text("System prompt v2\n", encoding="utf-8")
        dirty_code_cov = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--full-covered"])
        self.assertNotEqual(dirty_code_cov.returncode, 0)
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: prompt v2"], cwd=proj).returncode, 0)

        ver_blocked = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--verify-full"])
        self.assertNotEqual(ver_blocked.returncode, 0)
        ver_tag = self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--verify-full", "refs/tags/v1.0.0^{commit}"])
        self.assertEqual(ver_tag.returncode, 0, ver_tag.stdout + ver_tag.stderr)

    def test_19_debugging_compound_learn_and_playbook_budget(self):
        # 1) 母仓真实内置 playbooks 必须通过 ws learn check
        real_chk = self.run_cmd(["python3", str(WS_BIN), "learn", "check"], cwd=HUB)
        self.assertEqual(real_chk.returncode, 0, real_chk.stdout + real_chk.stderr)
        self.assertIn("经验飞轮容量与公私分离检查通过", real_chk.stdout)

        # 2) 项目模块契约水位报告（OK / WARN / OVER）与超限拦截
        proj = self.tmp_path / "learn-app"
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--mode", "build"]).returncode, 0)
        (proj / "src" / "orders").mkdir(parents=True)
        (proj / "src" / "orders" / "AGENTS.md").write_text(
            "\n".join(f"- rule {i}" for i in range(21)) + "\n",
            encoding="utf-8",
        )
        st = self.run_cmd(["python3", str(WS_BIN), "learn", "status", str(proj)])
        self.assertEqual(st.returncode, 0, st.stderr)
        self.assertIn("[WARN] src/orders/AGENTS.md: 21/25 非空行", st.stdout)

        # 3) WS_PLAYBOOKS_DIR 隔离验证：未登记卡、超行数卡、私有路径泄露与 >10 张卡均被 ws learn check 硬拦
        pb_tmp = self.tmp_path / "playbooks"
        pb_tmp.mkdir()
        (pb_tmp / "INDEX.md").write_text("# Index\n- F01.md\n", encoding="utf-8")
        (pb_tmp / "F01.md").write_text("# F01\nok\n", encoding="utf-8")
        env_pb = {**self.env, "WS_PLAYBOOKS_DIR": str(pb_tmp)}

        ok_res = subprocess.run(
            ["python3", str(WS_BIN), "learn", "check", str(proj)],
            text=True,
            capture_output=True,
            env=env_pb,
        )
        self.assertEqual(ok_res.returncode, 0, ok_res.stdout + ok_res.stderr)

        # 未登记在 INDEX.md 的孤儿卡 + 本机私有路径泄露拦截
        (pb_tmp / "F02-leak.md").write_text("# Leak\nSee /Users/alice/secret/app.py\n", encoding="utf-8")
        leak_res = subprocess.run(
            ["python3", str(WS_BIN), "learn", "check", str(proj)],
            text=True,
            capture_output=True,
            env=env_pb,
        )
        self.assertNotEqual(leak_res.returncode, 0)
        self.assertIn("未登记在 INDEX.md", leak_res.stderr)
        self.assertIn("私有用户目录路径", leak_res.stderr)

    def test_20_ws_line_acceptance_distill_env_and_preview_gate(self):
        proj = self.tmp_path / "preview-app"
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--mode", "build"]).returncode, 0)
        (proj / "deploy").mkdir(parents=True)
        gi = proj / ".gitignore"
        gi.write_text(gi.read_text(encoding="utf-8") + "repro/\n", encoding="utf-8")

        # 从避开系统临时端口段（Linux 32768+ / macOS 49152+）的区间挑选连续两个空闲端口，
        # 占用 base+1 并确认 base+2 空闲，防止 connect_ex 探活分配的临时源端口撞上 base+2 进入 TIME_WAIT
        busy_sock = None
        base_port = None
        for candidate in range(21000, 31000):
            s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe2:
                    probe2.settimeout(0.05)
                    if probe2.connect_ex(("127.0.0.1", candidate + 2)) == 0:
                        s1.close()
                        s2.close()
                        continue
                s1.bind(("127.0.0.1", candidate + 1))
                s2.bind(("127.0.0.1", candidate + 2))
                s2.close()
                s1.listen(1)
                busy_sock = s1
                base_port = candidate
                break
            except OSError:
                s1.close()
                s2.close()
        self.assertIsNotNone(busy_sock, "未在非临时端口区间找到连续两个空闲测试端口")
        try:
            (proj / "deploy" / "compose.yaml").write_text(
                f'services:\n  web:\n    ports:\n      - "127.0.0.1:${{WEB_PORT:-{base_port}}}:5175"\n',
                encoding="utf-8",
            )
            self.run_cmd(["git", "add", "-A"], cwd=proj)
            self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: init"], cwd=proj).returncode, 0)
            self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "design", str(proj)]).returncode, 0)
            self.run_cmd(["git", "add", "AGENTS.md"], cwd=proj)
            self.assertEqual(self.run_cmd(["git", "commit", "-m", "chore: lock design"], cwd=proj).returncode, 0)

            l_res = self.run_cmd(["python3", str(WS_BIN), "line", "new", "feat-ui", str(proj)])
            self.assertEqual(l_res.returncode, 0, l_res.stderr)
            self.assertIn("槽位 #2", l_res.stdout)
        finally:
            busy_sock.close()

        wt = self.tmp_path / "preview-app-lines" / "feat-ui"
        main_env = self.run_cmd(["python3", str(WS_BIN), "line", "env", str(proj)])
        self.assertEqual(main_env.returncode, 0, main_env.stderr)
        self.assertIn("WS_LINE_NAME=''", main_env.stdout)
        self.assertIn("WS_COMPOSE_PROJECT=preview-app", main_env.stdout)

        wt_env = self.run_cmd(["python3", str(WS_BIN), "line", "env", str(wt)])
        self.assertEqual(wt_env.returncode, 0, wt_env.stderr)
        self.assertIn("WS_LINE_NAME=feat-ui", wt_env.stdout)
        self.assertIn("WS_LINE_SLOT=2", wt_env.stdout)
        self.assertIn("WS_COMPOSE_PROJECT=preview-app-feat-ui", wt_env.stdout)

        # 未经 ws line 登记的野 git worktree 调用 ws line env 必须拒绝导出防误占主环境
        rogue_wt = self.tmp_path / "rogue-wt"
        self.assertEqual(self.run_cmd(["git", "worktree", "add", "--detach", str(rogue_wt), "HEAD"], cwd=proj).returncode, 0)
        rogue_env = self.run_cmd(["python3", str(WS_BIN), "line", "env", str(rogue_wt)])
        self.assertNotEqual(rogue_env.returncode, 0)
        self.run_cmd(["git", "worktree", "remove", "--force", str(rogue_wt)], cwd=proj)

        # distill 门禁：未提交改动、残留本地复现文件、模块 AGENTS.md 超限均硬拦截
        (wt / "src" / "ui").mkdir(parents=True)
        (wt / "src" / "ui" / "view.py").write_text("VIEW = 1\n", encoding="utf-8")
        self.assertNotEqual(self.run_cmd(["python3", str(WS_BIN), "line", "distill"], cwd=wt).returncode, 0)

        (wt / "src" / "ui" / "AGENTS.md").write_text("\n".join(f"- r{i}" for i in range(27)) + "\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt)
        self.assertEqual(self.run_cmd(["git", "commit", "--no-verify", "-m", "wip over limit"], cwd=wt).returncode, 0)
        over_dist = self.run_cmd(["python3", str(WS_BIN), "line", "distill"], cwd=wt)
        self.assertNotEqual(over_dist.returncode, 0)
        self.assertIn("超硬上限", over_dist.stderr)

        (wt / "src" / "ui" / "AGENTS.md").write_text("# UI\n- invariant 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "docs: slim agents"], cwd=wt).returncode, 0)

        (wt / "repro").mkdir()
        (wt / "repro" / "tmp.py").write_text("x = 1\n", encoding="utf-8")
        repro_dist = self.run_cmd(["python3", str(WS_BIN), "line", "distill"], cwd=wt)
        self.assertNotEqual(repro_dist.returncode, 0)
        self.assertIn("repro/", repro_dist.stderr)
        (wt / "repro" / "tmp.py").unlink()
        (wt / "repro").rmdir()

        # 正常 distill 通过；若随后又追加新提交，distilled_head 自动失效并再次拦截 sync
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "distill"], cwd=wt).returncode, 0)
        ls_distilled = self.run_cmd(["python3", str(WS_BIN), "line", "list", str(proj)]).stdout
        self.assertIn("已沉淀", ls_distilled)

        (wt / "src" / "ui" / "view.py").write_text("VIEW = 2\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: tweak after distill"], cwd=wt).returncode, 0)
        ls_stale = self.run_cmd(["python3", str(WS_BIN), "line", "list", str(proj)]).stdout
        self.assertIn("待验收/未沉淀", ls_stale)
        self.assertNotEqual(self.run_cmd(["python3", str(WS_BIN), "line", "sync"], cwd=wt).returncode, 0)

        # 若人工批准 --skip-distill 先合入了 main，未补跑 distill 前即便带 --discard 也拒绝删线防漏沉淀
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "sync", "--skip-distill"], cwd=wt).returncode, 0)
        rm_merged_undist = self.run_cmd(["python3", str(WS_BIN), "line", "rm", "feat-ui", str(proj), "--discard"])
        self.assertNotEqual(rm_merged_undist.returncode, 0)
        self.assertIn("尚未完成收线沉淀登记", rm_merged_undist.stderr)

        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "distill", "feat-ui", str(proj)]).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "rm", "feat-ui", str(proj)]).returncode, 0)

    def test_21_harness_guard_rules_silencers_stale_stubs_and_fast_lane(self):
        proj = self.tmp_path / "gardener-app"
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "init", str(proj), "--mode", "build"]).returncode, 0)
        for rel_d in ("src/domain", "src/infra", "src/orders/internal", "src/ui", "src/framework", "design/slices"):
            (proj / rel_d).mkdir(parents=True, exist_ok=True)

        # 1) 损坏的 `.harness-rules.json` 与危险 ignore_paths（越界/全仓逃逸）必须 fail-closed 拦截
        (proj / ".harness-rules.json").write_text("{bad json", encoding="utf-8")
        bad_rules = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)])
        self.assertEqual(bad_rules.returncode, 1)
        self.assertIn("JSON 解析失败", bad_rules.stderr)

        for bad_ign in (["../outside/**"], ["{a,../b}/**"], ["/etc/**"], ["**"], ["**/*"], ["*/**"], ["*/*"]):
            (proj / ".harness-rules.json").write_text(json.dumps({"ignore_paths": bad_ign}), encoding="utf-8")
            esc = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)])
            self.assertEqual(esc.returncode, 1, f"应拦截危险 ignore_paths={bad_ign}")

        # 写入合法声明式规则（ignore_paths + 单一事实源 public_entries + layer_bans + pattern_bans）
        rules_payload = {
            "ignore_paths": ["**/migrations/**", "src/generated/**", "deploy/assistant/**"],
            "public_entries": {
                "src/orders": "service.py",
            },
            "layer_bans": [
                {
                    "id": "domain-no-io",
                    "from": ["src/domain/**"],
                    "forbid_imports": ["src/infra/**", "httpx", "axios"],
                    "message": "纯计算核禁引 infra 或外部网络库",
                }
            ],
            "pattern_bans": [
                {
                    "id": "no-raw-use-effect",
                    "paths": ["src/**/*.{ts,tsx}"],
                    "allow_in": ["src/framework/**"],
                    "regex": r"\buseEffect\s*\(",
                    "message": "业务代码禁用原生 useEffect，请走 src/framework 受控封装",
                }
            ],
        }
        (proj / ".harness-rules.json").write_text(json.dumps(rules_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (proj / "src" / "infra" / "db.py").write_text("CONN = 1\n", encoding="utf-8")
        (proj / "src" / "infra" / "api.ts").write_text("export const fetchData = () => 1;\n", encoding="utf-8")
        (proj / "src" / "orders" / "service.py").write_text("def place():\n    return True\n", encoding="utf-8")
        (proj / "src" / "orders" / "internal" / "repo.py").write_text("def save():\n    return True\n", encoding="utf-8")
        # ignore_paths 应干净跳过只加不删迁移、自动生成客户端与非模块助手模板 AGENTS.md
        (proj / "src" / "orders" / "migrations").mkdir(parents=True)
        (proj / "src" / "orders" / "migrations" / "001.py").write_text("def downgrade():\n    raise NotImplementedError\n", encoding="utf-8")
        (proj / "src" / "generated").mkdir(parents=True)
        (proj / "src" / "generated" / "client.ts").write_text("// @ts-ignore\n// TODO: auto\n", encoding="utf-8")
        (proj / "deploy" / "assistant").mkdir(parents=True)
        (proj / "deploy" / "assistant" / "AGENTS.md").write_text("\n".join(f"- line {i}" for i in range(40)) + "\n", encoding="utf-8")

        # 2) public_entries（无模块 AGENTS.md 同样生效）+ layer_bans（Python 相对导入/三方库 + JS 导入）+ pattern_bans（含注释跳过与 allow_in）
        (proj / "src" / "domain" / "calc.py").write_text(
            "import httpx\n"
            "from ..infra import db\n"
            "from ..orders.internal import repo\n"
            "X = 1\n",
            encoding="utf-8",
        )
        (proj / "src" / "domain" / "calc.ts").write_text(
            "import axios from 'axios';\n"
            "import { fetchData } from '../infra/api';\n"
            "export const v = [axios, fetchData];\n",
            encoding="utf-8",
        )
        (proj / "src" / "ui" / "App.tsx").write_text(
            "// 注释里提及 useEffect( 不应误报\n"
            "export function App() { useEffect(() => {}); return null; }\n",
            encoding="utf-8",
        )
        (proj / "src" / "framework" / "hooks.ts").write_text(
            "export function useSafeEffect(fn: () => void) { useEffect(fn); }\n",
            encoding="utf-8",
        )
        rule_fail = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)])
        self.assertEqual(rule_fail.returncode, 1)
        self.assertIn("src/domain/calc.py:1: [domain-no-io]", rule_fail.stderr)
        self.assertIn("src/domain/calc.py:2: [domain-no-io]", rule_fail.stderr)
        self.assertIn("src/domain/calc.py:3: 模块边界越权导入", rule_fail.stderr)
        self.assertIn("src/domain/calc.ts:1: [domain-no-io]", rule_fail.stderr)
        self.assertIn("src/domain/calc.ts:2: [domain-no-io]", rule_fail.stderr)
        self.assertIn("src/ui/App.tsx:2: [no-raw-use-effect]", rule_fail.stderr)
        self.assertNotIn("src/ui/App.tsx:1:", rule_fail.stderr)
        self.assertNotIn("src/framework/hooks.ts", rule_fail.stderr)

        # 裸 rule-ok(id) 无原因不能豁免；附原因 rule-ok(id: 原因) 放行并进入账本
        (proj / "src" / "domain" / "calc.py").write_text(
            "import httpx  # rule-ok(domain-no-io)\n", encoding="utf-8"
        )
        self.assertEqual(self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)]).returncode, 1)
        (proj / "src" / "domain" / "calc.py").write_text(
            "import httpx  # rule-ok(domain-no-io: 迁移过渡)\n"
            "from ..orders.service import place\n"
            "X = (httpx, place)\n",
            encoding="utf-8",
        )
        (proj / "src" / "domain" / "calc.ts").write_text("export const v = 1;\n", encoding="utf-8")
        (proj / "src" / "ui" / "App.tsx").write_text(
            "// rule-ok(no-raw-use-effect: 历史桥接)\n"
            "export function App() { useEffect(() => {}); return null; }\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)]).returncode, 0)

        # 3) 全仓裸消音拦截 vs 精确错误码/字符串字面量放行
        (proj / "src" / "infra" / "silencers.py").write_text(
            "S = '# type: ignore # noqa'\n"
            "a = 1  # type: ignore\n"
            "b = 2  # noqa\n"
            "c = 3  # type: ignore[assignment]\n"
            "d = 4  # noqa: E501\n"
            "e = 5  # type: ignore  # ignore-ok(三方库缺桩)\n",
            encoding="utf-8",
        )
        (proj / "src" / "ui" / "silencers.ts").write_text(
            "export const lit = '@ts-ignore @ts-nocheck eslint-disable';\n"
            "// @ts-nocheck\n"
            "// @ts-ignore\n"
            "// @ts-expect-error\n"
            "// eslint-disable-next-line -- just ignore\n"
            "// @ts-expect-error: legacy SDK missing field\n"
            "// eslint-disable-next-line no-console\n"
            "// @ts-ignore ignore-ok(历史兼容)\n",
            encoding="utf-8",
        )
        sil_fail = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)])
        self.assertEqual(sil_fail.returncode, 1)
        self.assertNotIn("silencers.py:1:", sil_fail.stderr)
        self.assertIn("silencers.py:2:", sil_fail.stderr)
        self.assertIn("silencers.py:3:", sil_fail.stderr)
        self.assertNotIn("silencers.py:4:", sil_fail.stderr)
        self.assertNotIn("silencers.py:5:", sil_fail.stderr)
        self.assertNotIn("silencers.py:6:", sil_fail.stderr)
        self.assertNotIn("silencers.ts:1:", sil_fail.stderr)
        self.assertIn("silencers.ts:2:", sil_fail.stderr)
        self.assertIn("silencers.ts:3:", sil_fail.stderr)
        self.assertIn("silencers.ts:4:", sil_fail.stderr)
        self.assertIn("silencers.ts:5:", sil_fail.stderr)
        self.assertNotIn("silencers.ts:6:", sil_fail.stderr)
        self.assertNotIn("silencers.ts:7:", sil_fail.stderr)
        self.assertNotIn("silencers.ts:8:", sil_fail.stderr)

        (proj / "src" / "infra" / "silencers.py").unlink()
        (proj / "src" / "ui" / "silencers.ts").unlink()

        # 4) 过期 stub-ok(slice-01) 在 ws check 与 ws learn check 中均硬拦；未结项 slice-02 放行
        (proj / "design" / "slices" / "01-foundation.md").write_text("# S1\n- **状态**：`verified`\n", encoding="utf-8")
        (proj / "design" / "slices" / "02-next.md").write_text("# S2\n- **状态**：`building`\n", encoding="utf-8")
        (proj / "src" / "infra" / "db.py").write_text(
            "# TODO: active work stub-ok(slice-02)\n"
            "# TODO: forgotten debt stub-ok(slice-01)\n"
            "CONN = 1\n",
            encoding="utf-8",
        )
        stale_chk = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)])
        self.assertEqual(stale_chk.returncode, 1)
        self.assertIn("过期占位债", stale_chk.stderr)
        self.assertNotIn("slice-02", stale_chk.stderr)

        learn_fail = self.run_cmd(["python3", str(WS_BIN), "learn", "check", str(proj)])
        self.assertNotEqual(learn_fail.returncode, 0)
        self.assertIn("过期占位债", learn_fail.stderr)
        self.assertIn(".harness-rules.json: 忽略 3 / 公开入口 1 / 分层禁令 1 / 禁式规则 1", learn_fail.stdout)
        self.assertIn("豁免债", learn_fail.stdout)

        # 清理过期占位后全绿，验证修改 .harness-rules.json 自动令 --full-covered 凭证失效
        (proj / "src" / "infra" / "db.py").write_text("# TODO: active work stub-ok(slice-02)\nCONN = 1\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "feat: clean gardener baseline"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--record-full"]).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--full-covered"]).returncode, 0)

        rules_payload["pattern_bans"].append({
            "id": "no-eval",
            "paths": ["src/**/*.py"],
            "regex": r"\beval\s*\(",
            "message": "禁 eval",
        })
        (proj / ".harness-rules.json").write_text(json.dumps(rules_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertNotEqual(self.run_cmd(["python3", str(WS_BIN), "check", str(proj), "--full-covered"]).returncode, 0)

        # 验证 Python docstring 中解释禁式（含中文字符）不触发 pattern_bans 误报，但同行或后续真实调用仍被拦截
        (proj / "src" / "infra" / "doc_safe.py").write_text(
            '"""模块说明：切勿使用 eval( 执行不可信文本。"""\n\n\n'
            "def safe_fn() -> int:\n"
            '    """函数说明：不要调 eval( 否则有注入风险。"""\n'
            "    return 1\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)]).returncode, 0)
        (proj / "src" / "infra" / "doc_safe.py").write_text(
            'def bad_inline() -> int: "中文 docstring 禁 eval("; return eval("1")\n',
            encoding="utf-8",
        )
        inline_fail = self.run_cmd(["python3", str(HARNESS_GUARD), str(proj)])
        self.assertEqual(inline_fail.returncode, 1)
        self.assertIn("doc_safe.py:1: [no-eval]", inline_fail.stderr)
        (proj / "src" / "infra" / "doc_safe.py").unlink()

        self.run_cmd(["git", "add", ".harness-rules.json"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "chore: tighten rules"], cwd=proj).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "mode", "design", str(proj)]).returncode, 0)
        self.run_cmd(["git", "add", "AGENTS.md"], cwd=proj)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "chore: lock design"], cwd=proj).returncode, 0)

        # 5) 信任阶梯快车道（--lane fast）：禁止绕过未拍板同名切片；正常快车道可完整闭环
        (proj / "design" / "slices" / "03-risky.md").write_text("# S3\n- **状态**：`proposed`\n", encoding="utf-8")
        bypass_fast = self.run_cmd(["python3", str(WS_BIN), "line", "new", "03-risky", str(proj), "--lane", "fast"])
        self.assertNotEqual(bypass_fast.returncode, 0)
        self.assertIn("未拍板", bypass_fast.stderr)
        (proj / "design" / "slices" / "03-risky.md").unlink()

        fast_new = self.run_cmd(["python3", str(WS_BIN), "line", "new", "fix-rules", str(proj), "--lane", "fast", "--scope", "src/infra"])
        self.assertEqual(fast_new.returncode, 0, fast_new.stderr)
        self.assertIn("快车道 fast", fast_new.stdout)
        fast_ls = self.run_cmd(["python3", str(WS_BIN), "line", "list", str(proj)]).stdout
        self.assertIn("lane=fast", fast_ls)

        wt = self.tmp_path / "gardener-app-lines" / "fix-rules"
        (wt / ".gitignore").write_text(
            ((wt / ".gitignore").read_text(encoding="utf-8") if (wt / ".gitignore").exists() else "")
            + ".hypothesis/\n",
            encoding="utf-8",
        )
        (wt / "src" / "infra" / "db.py").write_text("CONN = 2\n", encoding="utf-8")
        self.run_cmd(["git", "add", "-A"], cwd=wt)
        self.assertEqual(self.run_cmd(["git", "commit", "-m", "refactor: clean stub"], cwd=wt).returncode, 0)
        # 属性测试自动生成的 .hypothesis/ 缓存不得误拦 ws line distill 与 ws line rm
        (wt / ".hypothesis" / "constants").mkdir(parents=True)
        (wt / ".hypothesis" / "constants" / "deadbeef").write_text("cache\n", encoding="utf-8")
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "distill"], cwd=wt).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "sync"], cwd=wt).returncode, 0)
        self.assertEqual(self.run_cmd(["python3", str(WS_BIN), "line", "rm", "fix-rules", str(proj)]).returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
