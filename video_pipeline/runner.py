"""Main orchestrator for the declarative video pipeline."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from loguru import logger

from video_pipeline import assembler
from video_pipeline.assembler import AssemblyError, ClipSpec
from video_pipeline.config import PipelineConfig, UpscaleConfig, load_config, render_template
from video_pipeline.director_base import Director
from video_pipeline.directors.freevideo import FreeVideoDirector
from video_pipeline.directors.h3 import H3Director
from video_pipeline.notify import TelegramNotifier, TelegramNotifierError
from video_pipeline.quality import QualityGate
from video_pipeline.state import ItemStatus, StateManager

_shutdown_requested = False


def _handle_sigint(signum: int, frame: object) -> None:
    """Set shutdown flag on SIGINT for graceful exit."""
    global _shutdown_requested
    logger.warning("Shutdown requested (signal {}), saving state...", signum)
    _shutdown_requested = True


def _discover_images(source_dir: Path) -> list[Path]:
    """Discover jpg and png images in source directory."""
    if not source_dir.exists():
        logger.warning("Source directory does not exist: {}", source_dir)
        return []
    images = sorted(source_dir.glob("*.jpg")) + sorted(source_dir.glob("*.png"))
    return images


def _director_upscale_config(config: PipelineConfig) -> UpscaleConfig:
    """Build upscale settings for the director without mutating the pipeline config."""
    upscale = config.upscale
    free_urls = (
        list(upscale.free_vram_urls)
        if upscale.free_vram_urls
        else [config.director_config.comfy_url]
    )
    return upscale.model_copy(update={"free_vram_urls": free_urls})


def _get_director(config: PipelineConfig) -> Director:
    """Factory for director implementations."""
    match config.director:
        case "h3":
            kwargs: dict[str, object] = {
                "output_dir": config.output_dir,
                "comfy_url": config.director_config.comfy_url,
                "upscale_config": _director_upscale_config(config),
            }
            if config.director_config.workflow_path is not None:
                kwargs["workflow_path"] = config.director_config.workflow_path
            return H3Director(**kwargs)
        case "freevideo":
            return FreeVideoDirector(
                output_dir=config.output_dir,
                comfy_url=config.director_config.comfy_url,
                freevideo_config=config.freevideo,
            )
        case _:
            raise ValueError(f"Unknown director: {config.director}")


def _maybe_langfuse_observation(name: str):
    """Return a Langfuse observation context manager if configured."""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return _null_context()

    try:
        from langfuse import get_client

        client = get_client()
        return client.start_as_current_observation(name=name)
    except Exception:
        return _null_context()


class _null_context:
    """No-op context manager."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


def run_pipeline(
    config: PipelineConfig,
    *,
    resume: bool = False,
    dry_run: bool = False,
) -> int:
    """Execute the video pipeline.

    Args:
        config: Validated pipeline configuration.
        resume: Whether to resume from saved state.
        dry_run: Print planned jobs without executing.

    Returns:
        Exit code (0 success, 1 failure).
    """
    global _shutdown_requested

    state_mgr = StateManager(config.recovery.state_file, config.project)
    state = state_mgr.load() if resume or config.recovery.resume_from_state else state_mgr._fresh_state()
    if not resume and not config.recovery.resume_from_state:
        state_mgr.save(state)

    images = _discover_images(config.source_dir)
    total_jobs = len(images) * len(config.outputs)
    done_count = 0

    notifier: TelegramNotifier | None = None
    if not dry_run:
        try:
            notifier = TelegramNotifier(progress_every_min=config.delivery.progress_every_min)
        except TelegramNotifierError as exc:
            logger.warning("Telegram notifier unavailable: {}", exc)

    director = _get_director(config) if not dry_run else None
    quality = QualityGate()
    assembled_clips: list[ClipSpec] = []
    first_source_image: Path | None = None

    logger.info(
        "Pipeline {}: {} images x {} outputs = {} jobs",
        config.project,
        len(images),
        len(config.outputs),
        total_jobs,
    )

    for image_path in images:
        image_name = image_path.name
        for output_spec in config.outputs:
            format_key = output_spec.format

            if _shutdown_requested:
                logger.info("Shutdown: saving state and exiting")
                state_mgr.save(state)
                return 0

            current_status = state_mgr.get_item_status(image_name, format_key)
            if current_status == ItemStatus.DONE.value:
                done_count += 1
                continue

            job_label = f"{image_name} [{format_key}]"
            if dry_run:
                logger.info(
                    "DRY-RUN: would render {} motion={} duration={}s -> {}",
                    job_label,
                    output_spec.motion,
                    output_spec.duration_sec,
                    config.output_dir,
                )
                continue

            attempts = 0
            max_retries = config.recovery.max_retries

            while attempts <= max_retries:
                if _shutdown_requested:
                    state_mgr.save(state)
                    return 0

                attempts += 1
                state_mgr.update_item(
                    image_name,
                    format_key,
                    status=ItemStatus.RENDERING.value,
                    attempts=attempts,
                    started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )

                try:
                    with _maybe_langfuse_observation("render"):
                        job = director.prepare(image_path, output_spec)
                        prompt_id = director.submit(job)
                        result = director.poll_until_done_with_job(
                            prompt_id,
                            timeout_sec=config.quality_gates.max_render_sec,
                            job=job,
                        )

                    report = quality.check_all(
                        result.video_path,
                        image_path,
                        config.quality_gates,
                        target_duration_sec=float(output_spec.duration_sec),
                        include_banding=config.quality_gates.include_banding,
                        banding_max_score=config.quality_gates.banding_max_score,
                    )

                    if not report.overall_passed:
                        reasons = "; ".join(c.message for c in report.checks if not c.passed)
                        logger.error("Quality gate failed for {}: {}", job_label, reasons)
                        if attempts > max_retries:
                            state_mgr.mark_failed(image_name, format_key, f"quality: {reasons}")
                        else:
                            time.sleep(config.recovery.retry_backoff_sec * attempts)
                            continue
                        break

                    final_path = director.upscale(result.video_path, output_spec.format)
                    delivery_path = final_path
                    interpolated = False

                    if config.interpolation.enabled:
                        interpolated_path = final_path.with_name(
                            f"{final_path.stem}_interp{final_path.suffix}"
                        )
                        try:
                            assembler.interpolate(
                                final_path,
                                interpolated_path,
                                config.interpolation.target_fps,
                                comfyui_url=config.interpolation.comfyui_url,
                            )
                            delivery_path = interpolated_path
                            interpolated = True
                        except AssemblyError as exc:
                            logger.warning("Interpolation skipped for {}: {}", job_label, exc)

                    if interpolated:
                        duration_check = quality.check_duration(
                            delivery_path,
                            float(output_spec.duration_sec),
                        )
                        if not duration_check.passed:
                            logger.error(
                                "Duration check failed for {} after interpolation: {}",
                                job_label,
                                duration_check.message,
                            )
                            if attempts > max_retries:
                                state_mgr.mark_failed(
                                    image_name,
                                    format_key,
                                    f"quality: {duration_check.message}",
                                )
                            else:
                                time.sleep(config.recovery.retry_backoff_sec * attempts)
                                continue
                            break

                    size_mb = delivery_path.stat().st_size / (1024 * 1024)
                    ar_check = quality.check_ar(
                        delivery_path,
                        image_path,
                        config.quality_gates.max_ar_drift,
                    )

                    deliver_clip = not (
                        config.assembly.enabled and not config.assembly.deliver_per_clip
                    )
                    if notifier and deliver_clip:
                        caption = render_template(
                            config.delivery.caption_template,
                            name=image_path.stem,
                            format=format_key,
                            duration=output_spec.duration_sec,
                        )
                        try:
                            notifier.send_video(
                                delivery_path,
                                caption,
                                config.delivery.telegram_chat,
                            )
                        except Exception as exc:
                            logger.error("Telegram delivery failed: {}", exc)
                            if attempts > max_retries:
                                state_mgr.mark_failed(image_name, format_key, f"telegram: {exc}")
                                break
                            time.sleep(config.recovery.retry_backoff_sec * attempts)
                            continue

                    state_mgr.mark_done(
                        image_name,
                        format_key,
                        str(delivery_path),
                        duration_sec=result.render_sec,
                        size_mb=round(size_mb, 2),
                        ar_drift=ar_check.actual,
                        interpolated=interpolated,
                    )
                    assembled_clips.append(
                        ClipSpec(
                            path=delivery_path,
                            audio_path=final_path if interpolated else None,
                        )
                    )
                    if first_source_image is None:
                        first_source_image = image_path
                    done_count += 1
                    logger.info("Completed {} -> {}", job_label, delivery_path)

                    if notifier:
                        pct = int((done_count / total_jobs) * 100) if total_jobs else 100
                        progress_text = render_template(
                            config.delivery.progress_template,
                            project=config.project,
                            done=done_count,
                            all=total_jobs,
                            pct=pct,
                        )
                        notifier.send_progress(progress_text, config.delivery.telegram_chat)
                        print(f"Progress: {progress_text}")

                    break

                except Exception as exc:
                    logger.exception("Job failed {} attempt {}/{}: {}", job_label, attempts, max_retries + 1, exc)
                    if attempts > max_retries:
                        state_mgr.mark_failed(image_name, format_key, str(exc))
                    else:
                        time.sleep(config.recovery.retry_backoff_sec * attempts)

    if (
        not dry_run
        and config.assembly.enabled
        and assembled_clips
        and first_source_image is not None
    ):
        assembled_path = config.output_dir / f"{config.assembly.output_name}.mp4"
        try:
            width, height = assembler.target_resolution(assembled_clips)
            target_ar_value = width / height if height else None
            assembler.concat_clips(assembled_clips, assembled_path, target_ar_value)
        except AssemblyError as exc:
            logger.error("Assembly failed: {}", exc)
        else:
            assembled_duration = 0.0
            for image_path in images:
                for output_spec in config.outputs:
                    if (
                        state_mgr.get_item_status(image_path.name, output_spec.format)
                        == ItemStatus.DONE.value
                    ):
                        assembled_duration += float(output_spec.duration_sec)

            asm_report = quality.check_all(
                assembled_path,
                None,
                config.quality_gates,
                target_duration_sec=assembled_duration if assembled_duration > 0 else None,
                include_banding=config.quality_gates.include_banding,
                banding_max_score=config.quality_gates.banding_max_score,
                expected_ar=target_ar_value,
            )
            if not asm_report.overall_passed:
                reasons = "; ".join(c.message for c in asm_report.checks if not c.passed)
                logger.error("Assembled video quality gate failed: {}", reasons)
            else:
                asm_size_mb = assembled_path.stat().st_size / (1024 * 1024)
                if notifier and config.assembly.deliver_assembled:
                    asm_caption = render_template(
                        config.delivery.caption_template,
                        name=config.assembly.output_name,
                        format="assembled",
                        duration=int(assembled_duration),
                        assembled=config.assembly.output_name,
                        clip_count=len(assembled_clips),
                    )
                    try:
                        notifier.send_video(
                            assembled_path,
                            asm_caption,
                            config.delivery.telegram_chat,
                        )
                    except Exception as exc:
                        logger.error("Telegram delivery of assembled video failed: {}", exc)
                state_mgr.mark_assembly_done(
                    str(assembled_path),
                    name=config.assembly.output_name,
                    clip_count=len(assembled_clips),
                    size_mb=round(asm_size_mb, 2),
                )
                logger.info(
                    "Assembled {} clips -> {}",
                    len(assembled_clips),
                    assembled_path,
                )

    if notifier and not dry_run and total_jobs > 0:
        final_text = render_template(
            config.delivery.final_template,
            project=config.project,
            done=done_count,
            all=total_jobs,
        )
        try:
            notifier.send_text(final_text, config.delivery.telegram_chat)
        except Exception as exc:
            logger.warning("Failed to send final summary: {}", exc)

    logger.info("Pipeline finished: {}/{} jobs done", done_count, total_jobs)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Declarative video pipeline runner")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to pipeline YAML config",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from saved state file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned jobs without executing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    signal.signal(signal.SIGINT, _handle_sigint)

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (ValueError, FileNotFoundError) as exc:
        logger.error("Config error: {}", exc)
        return 1

    return run_pipeline(config, resume=args.resume, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
