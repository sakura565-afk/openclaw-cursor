# Tool Inventory

Auto-discovered tools in `scripts/` and `src/` based on call-pattern scanning.

- Total discovered tools: **19**

## batch_image_optimizer

- Module path: `scripts.batch_image_optimizer`
- Capabilities: `web_calls`
- Usage examples:
  - `scripts/batch_image_optimizer.py:141` `requests.post` - `requests.post(endpoint, headers=headers, json=payload, timeout=timeout)`

## comfy_video_pipeline

- Module path: `scripts.comfy_video_pipeline`
- Capabilities: `exec_calls`
- Usage examples:
  - `scripts/comfy_video_pipeline.py:567` `subprocess.run` - `subprocess.run(cmd, check=True, capture_output=True, text=True)`

## context_split

- Module path: `scripts.context_split`
- Capabilities: `file_operations`
- Usage examples:
  - `scripts/context_split.py:704` `read_text` - `Path(args.context_file).read_text(encoding="utf-8")`

## memory_cleanup

- Module path: `scripts.memory_cleanup`
- Capabilities: `file_operations`
- Usage examples:
  - `scripts/memory_cleanup.py:449` `write_text` - `write["path"].write_text(write["content"], encoding="utf-8")`

## ollama_benchmark

- Module path: `scripts.ollama_benchmark`
- Capabilities: `exec_calls`
- Usage examples:
  - `scripts/ollama_benchmark.py:100` `subprocess.run` - `subprocess.run( args, capture_output=True, text=True, check=False, )`

## ollama_model_manager

- Module path: `scripts.ollama_model_manager`
- Capabilities: `exec_calls`
- Usage examples:
  - `scripts/ollama_model_manager.py:431` `subprocess.run` - `subprocess.run(command, text=True, capture_output=True, check=False)`
  - `scripts/ollama_model_manager.py:485` `subprocess.Popen` - `subprocess.Popen( command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, )`

## ollama_monitor

- Module path: `scripts.ollama_monitor`
- Capabilities: `exec_calls`, `file_operations`
- Usage examples:
  - `scripts/ollama_monitor.py:166` `write_text` - `state_path(root).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")`
  - `scripts/ollama_monitor.py:306` `subprocess.run` - `subprocess.run(command, capture_output=True, text=True, check=False)`

## optimize_context

- Module path: `scripts.optimize_context`
- Capabilities: `file_operations`
- Usage examples:
  - `scripts/optimize_context.py:213` `read_text` - `read_text(session_log_path)`
  - `scripts/optimize_context.py:232` `read_text` - `read_text(session_log_path)`
  - `scripts/optimize_context.py:255` `read_text` - `read_text(path)`
  - `scripts/optimize_context.py:321` `read_text` - `read_text(path)`

## proactive_scout

- Module path: `scripts.proactive_scout`
- Capabilities: `exec_calls`
- Usage examples:
  - `scripts/proactive_scout.py:568` `subprocess.Popen` - `subprocess.Popen( [sys.executable, str(Path(__file__).resolve()), "_worker", "--job-path", str(job_path), "--scout-dir", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True, )`

## queue_manager

- Module path: `scripts.queue_manager`
- Capabilities: `exec_calls`
- Usage examples:
  - `scripts/queue_manager.py:23` `subprocess.run` - `subprocess.run( ["python", "cursor_cloud_agent.py", "status"], capture_output=True, text=True, timeout=10, cwd=str(Path(__file__).parent.parent) )`
  - `scripts/queue_manager.py:39` `subprocess.run` - `subprocess.run( [ "python", "cursor_cloud_agent.py", "launch", "--api-key", API_KEY, "--repo", REPO, "--ref", REF, "--prompt", prompt, "--auto-pr" ], capture_output=True, text=True, timeout=30, cwd=str(Path(__file__).parent.parent.parent) )`
  - `scripts/queue_manager.py:71` `subprocess.run` - `subprocess.run( [ "python", "cursor_cloud_agent.py", "poll", "--api-key", API_KEY, "--agent-id", agent_id ], capture_output=True, text=True, timeout=60, cwd=str(Path(__file__).parent.parent.parent) )`

## sync_obsidian

- Module path: `scripts.sync_obsidian`
- Capabilities: `file_operations`
- Usage examples:
  - `scripts/sync_obsidian.py:286` `read_text` - `read_text(memory_path)`
  - `scripts/sync_obsidian.py:298` `read_text` - `read_text(generated_note_path)`
  - `scripts/sync_obsidian.py:315` `write_text` - `write_text(generated_note_path, generated_after)`
  - `scripts/sync_obsidian.py:386` `write_text` - `write_text(memory_path, memory_text)`
  - `scripts/sync_obsidian.py:406` `write_text` - `write_text(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")`

## telegram_sender

- Module path: `scripts.telegram_sender`
- Capabilities: `message_sends`
- Usage examples:
  - `scripts/telegram_sender.py:315` `sender.send_photo` - `sender.send_photo(args.image, caption=args.caption)`
  - `scripts/telegram_sender.py:317` `sender.send_group` - `sender.send_group(args.images, caption=args.caption)`
  - `scripts/telegram_sender.py:319` `sender.send_document` - `sender.send_document(args.file, caption=args.caption)`

## video_thumbnail_generator

- Module path: `scripts.video_thumbnail_generator`
- Capabilities: `exec_calls`
- Usage examples:
  - `scripts/video_thumbnail_generator.py:40` `subprocess.run` - `subprocess.run(command, check=True, capture_output=True, text=True)`

## cross_bot_sync

- Module path: `src.coordination.cross_bot_sync`
- Capabilities: `file_operations`
- Usage examples:
  - `src/coordination/cross_bot_sync.py:150` `os.open` - `os.open( self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644, )`

## idea_pipeline

- Module path: `src.ideation.idea_pipeline`
- Capabilities: `exec_calls`, `file_operations`
- Usage examples:
  - `src/ideation/idea_pipeline.py:193` `open` - `self._log_path().open("w", encoding="utf-8")`
  - `src/ideation/idea_pipeline.py:239` `subprocess.run` - `subprocess.run( ["git", "status", "--short", "--branch"], check=False, capture_output=True, text=True, cwd=self.project_root, )`

## session_monitor

- Module path: `src.monitoring.session_monitor`
- Capabilities: `exec_calls`
- Usage examples:
  - `src/monitoring/session_monitor.py:18` `subprocess.run` - `subprocess.run( ["npx", "openclaw", "status", "--json"], capture_output=True, text=True, timeout=30, shell=True )`

## task_runner

- Module path: `src.openclaw_orchestration.task_runner`
- Capabilities: `exec_calls`
- Usage examples:
  - `src/openclaw_orchestration/task_runner.py:372` `subprocess.run` - `subprocess.run( command, cwd=self.base_dir, env=env, capture_output=True, text=True, timeout=timeout, check=False, )`
  - `src/openclaw_orchestration/task_runner.py:399` `subprocess.run` - `subprocess.run( str(command), cwd=self.base_dir, env=env, capture_output=True, text=True, timeout=timeout, shell=True, check=False, )`

## auto_engine

- Module path: `src.self_improvement.auto_engine`
- Capabilities: `exec_calls`
- Usage examples:
  - `src/self_improvement/auto_engine.py:105` `subprocess.run` - `subprocess.run( list(command), check=False, capture_output=True, text=True, )`

## proactive_watcher

- Module path: `src.skills.proactive_watcher`
- Capabilities: `exec_calls`
- Usage examples:
  - `src/skills/proactive_watcher.py:161` `subprocess.run` - `subprocess.run( [sys.executable, "-m", "py_compile", str(python_file)], capture_output=True, text=True, check=False, )`
