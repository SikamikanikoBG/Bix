"""bix command line.

  bix server            run the cloud service (portal + device API + worker)
  bix server admin      create an admin account
  bix server code       make a device enrollment code
  bix device            run the device agent (+ local room panel)
  bix device enroll     register this device with a server
  bix device mics       list microphones
  bix dev               both on one machine (laptop trial)
  bix doctor            check configuration and providers
  bix eval              word error rate of a transcript against a reference
"""

from __future__ import annotations

import argparse
import getpass
import logging
import secrets
import sys
import threading
import time
import webbrowser

log = logging.getLogger("bix")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="bix", description="Bix: записва срещата, разпознава кой говори и праща минутите.")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("server", help="облачната услуга")
    sp.add_argument("--config")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--no-worker", action="store_true", help="само HTTP (обработката е в отделен процес)")
    ssub = sp.add_subparsers(dest="sub")
    a = ssub.add_parser("admin", help="създай администратор")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="")
    a.add_argument("--org", default="")
    c = ssub.add_parser("code", help="код за регистрация на устройство")
    c.add_argument("--room", default="", help="име на залата (създава се, ако я няма)")
    c.add_argument("--hours", type=int, default=24)
    ssub.add_parser("worker", help="само обработка (без HTTP)")
    te = ssub.add_parser("test-email", help="пробен имейл с настройките на сървъра (SMTP или Microsoft 365)")
    te.add_argument("--to", required=True)
    ssub.add_parser("check", help="провери връзките: реч, езиков модел, имейл, хранилище, календар")

    dp = sub.add_parser("device", help="агентът на устройството")
    dp.add_argument("--config")
    dp.add_argument("--face", action="store_true", help="лице на дисплея (SDL2, без браузър)")
    dsub = dp.add_subparsers(dest="sub")
    e = dsub.add_parser("enroll", help="регистрирай устройството")
    e.add_argument("--server", required=True)
    e.add_argument("--code", required=True)
    dsub.add_parser("mics", help="списък с микрофони")

    dv = sub.add_parser("dev", help="сървър и устройство на една машина (лаптоп)")
    dv.add_argument("--admin-email", default="")
    dv.add_argument("--no-browser", action="store_true")
    dv.add_argument("--face", action="store_true")

    doc = sub.add_parser("doctor", help="провери настройките")
    doc.add_argument("--config")

    ev = sub.add_parser("eval", help="WER/CER спрямо еталонен текст")
    ev.add_argument("reference", help="файл с еталонния текст")
    ev.add_argument("hypothesis", help="файл с текста за проверка (.txt или transcript.json)")

    args = ap.parse_args(argv)
    try:
        _dispatch(args)
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception as e:  # a person reads this: no traceback, a clear reason
        log.debug("command failed", exc_info=True)
        sys.exit(f"Грешка: {_explain(e)}")


def _explain(e: Exception) -> str:
    import tomllib

    import httpx

    if isinstance(e, tomllib.TOMLDecodeError):
        return f"конфигурационният файл не е валиден TOML: {e}"
    if isinstance(e, httpx.ConnectError):
        return f"няма връзка със сървъра ({e.request.url.scheme}://{e.request.url.host}:{e.request.url.port})."
    if isinstance(e, httpx.HTTPError):
        return f"мрежова грешка: {e}"
    if isinstance(e, FileNotFoundError):
        return f"няма такъв файл: {e.filename}"
    return str(e) or type(e).__name__


def _dispatch(args) -> None:
    if args.cmd == "server":
        cmd_server(args)
    elif args.cmd == "device":
        cmd_device(args)
    elif args.cmd == "dev":
        cmd_dev(args)
    elif args.cmd == "doctor":
        cmd_doctor(args)
    elif args.cmd == "eval":
        cmd_eval(args)


# --- server ----------------------------------------------------------------------


def _server(args):
    from bix.common import logs
    from bix.server import config as scfg
    from bix.server.core import Server

    cfg, path = scfg.load(getattr(args, "config", None))
    logs.setup(cfg.dir / "logs", "server", getattr(args, "verbose", False))
    if getattr(args, "host", None):
        cfg.http.host = args.host
    if getattr(args, "port", None):
        cfg.http.port = args.port
    return Server.create(cfg, path)


def _test_email(srv, to: str) -> None:
    from bix.server.integrations import DeliveryError, smtp_send

    html = "<p>Пробен имейл от Bix. Ако го виждате, минутите ще стигат до хората.</p>"
    try:
        if srv.graph.enabled and srv.cfg.graph.sender_mailbox:
            srv.graph.send_mail([to], "Bix: пробен имейл", html)
            via = f"Microsoft 365 от {srv.cfg.graph.sender_mailbox}"
        else:
            smtp_send(srv.cfg.smtp, [to], "Bix: пробен имейл", html, "Пробен имейл от Bix.")
            via = f"SMTP {srv.cfg.smtp.host}:{srv.cfg.smtp.port} от {srv.cfg.smtp.sender or srv.cfg.smtp.username}"
    except DeliveryError as e:
        print(f"✗ Не тръгна: {e}")
        raise SystemExit(1) from e
    print(f"✓ Изпратен до {to} през {via}.")


def _check_services(srv) -> None:
    """One line per moving part, for the first deploy in a new network."""
    from bix.server.api_portal import service_status

    ok = True
    for name, st in service_status(srv).items():
        if not isinstance(st, dict) or "ok" not in st:
            continue
        ok &= bool(st["ok"])
        detail = st.get("detail", "")
        if isinstance(detail, dict):
            detail = ", ".join(f"{k} {'✓' if v else 'липсва'}" for k, v in detail.items())
        print(f"{'✓' if st['ok'] else '✗'} {name:12} {st.get('provider', '')}: {detail}")
    raise SystemExit(0 if ok else 1)


def cmd_server(args) -> None:
    srv = _server(args)
    if args.sub == "admin":
        return _create_admin(srv, args.email, args.name, args.org)
    if args.sub == "code":
        code = _enrollment_code(srv, args.room, args.hours)
        print(f"Код: {code}  (валиден {args.hours} ч)\nНа устройството: bix device enroll --server {srv.cfg.http.public_url} --code {code}")
        return
    if args.sub == "test-email":
        return _test_email(srv, args.to)
    if args.sub == "check":
        return _check_services(srv)
    if args.sub == "worker":
        from bix.server.worker import Worker

        w = Worker(srv)
        w.start()
        log.info("worker running")
        while True:
            time.sleep(3600)
    _serve(srv, run_worker=not args.no_worker)


def _serve(srv, run_worker: bool = True) -> None:
    import uvicorn

    from bix.server.app import create_app

    app = create_app(srv, run_worker=run_worker)
    log.info("Bix server on http://%s:%d (config %s)", srv.cfg.http.host, srv.cfg.http.port, srv.cfg_path)
    # keep-alive longer than any client's polling interval (devices poll every 5-15 s);
    # uvicorn's 5 s default races with them and drops requests on reused connections.
    uvicorn.run(
        app, host=srv.cfg.http.host, port=srv.cfg.http.port, log_level="warning", proxy_headers=True,
        forwarded_allow_ips=",".join(srv.cfg.http.trusted_proxies), timeout_keep_alive=65,
    )


def _create_admin(srv, email: str, name: str = "", org_name: str = "", password: str | None = None) -> str:
    from sqlalchemy import func, select

    from bix.server.auth import hash_password, password_problems
    from bix.server.db import Org, User, audit, first_org

    with srv.db.session() as s:
        if s.scalars(select(User).where(func.lower(User.email) == email.lower())).first():
            sys.exit(f"Вече има потребител {email}")
    if password is None:
        password = getpass.getpass("Парола (поне 12 знака): ")
        if problem := password_problems(password):
            sys.exit(problem)
    with srv.db.session() as s:
        org = first_org(s)
        if not org:
            org = Org(name=org_name or "Организация")
            s.add(org)
            s.flush()
        if s.scalars(select(User).where(func.lower(User.email) == email.lower())).first():
            sys.exit(f"Вече има потребител {email}")
        s.add(User(org_id=org.id, email=email.lower(), name=name, role="admin", password_hash=hash_password(password)))
        audit(s, "cli", "user.create_admin", org_id=org.id, email=email)
    print(f"Администратор {email} е създаден в „{org.name}“.")
    return password


def _enrollment_code(srv, room_name: str, hours: int = 24) -> str:
    from sqlalchemy import select

    from bix.server.auth import token_hash
    from bix.server.db import EnrollmentCode, Room, audit, first_org

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    with srv.db.session() as s:
        org = first_org(s)
        if not org:
            sys.exit("Първо създайте администратор: bix server admin --email ...")
        room = None
        if room_name:
            room = s.scalars(select(Room).where(Room.org_id == org.id, Room.name == room_name)).first()
            if not room:
                room = Room(org_id=org.id, name=room_name)
                s.add(room)
                s.flush()
        s.add(EnrollmentCode(code_hash=token_hash(raw), org_id=org.id, room_id=room.id if room else None, created_by="cli", expires_at=time.time() + hours * 3600))
        audit(s, "cli", "device.enrollment_code", org_id=org.id, room=room_name)
    return "-".join(raw[i : i + 4] for i in range(0, 12, 4))


# --- device ------------------------------------------------------------------------


def cmd_device(args) -> None:
    from bix.common import logs
    from bix.device import config as dcfg

    cfg, path = dcfg.load(args.config)
    logs.setup(cfg.dir / "logs", "device", args.verbose)
    if args.sub == "mics":
        from bix.device.capture import list_inputs

        mics = list_inputs()
        if not mics:
            sys.exit("Няма открити микрофони. Свържете микрофон (на Bix: reSpeaker XVF3800 по USB) и опитайте пак.")
        for d in mics:
            print(f"{'*' if d['default'] else ' '} {d['index']:>3}  {d['name']}  [{d['api']}, {int(d['rate'])} Hz]")
        return
    if args.sub == "enroll":
        from bix.device.client import ServerClient

        data = ServerClient(cfg).enroll(args.server, args.code)
        print(f"Регистрирано: {data['org']} · {data['room'] or 'без зала'} · id {data['device_id']}")
        return
    _run_device(cfg, face=args.face)


def _run_device(cfg, face: bool = False, open_browser: bool | None = None) -> None:
    from bix.device.agent import Agent

    agent = Agent(cfg)

    def on_event(ev) -> None:
        # "restart" from the portal: exit; systemd (Restart=always) starts us again.
        if ev.type == "restart":
            import os

            log.warning("restart requested by the server")
            agent.stop_all()
            os._exit(3)

    agent.bus.on(on_event)
    agent.start()
    if not agent.client.enrolled:
        log.warning("Устройството не е регистрирано: bix device enroll --server URL --code КОД (или от панела)")
    if cfg.panel.enabled:
        import uvicorn

        from bix.device.panel import create_panel

        app = create_panel(agent)
        server = uvicorn.Server(uvicorn.Config(app, host=cfg.panel.host, port=cfg.panel.port, log_level="warning", timeout_keep_alive=65))
        threading.Thread(target=server.run, name="bix-panel", daemon=True).start()
        log.info("room panel on http://%s:%d", cfg.panel.host, cfg.panel.port)
        if open_browser if open_browser is not None else cfg.panel.open_browser:
            threading.Timer(1.5, lambda: webbrowser.open(f"http://{cfg.panel.host}:{cfg.panel.port}")).start()
    try:
        if face:
            from bix.device.face import run_face

            run_face(agent)  # SDL2 needs the main thread
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop_all()
        # give the uplink a moment to register the end of the meeting
        time.sleep(1)


# --- dev: both on one laptop -------------------------------------------------------


def cmd_dev(args) -> None:
    """Server + device on this machine: a laptop trial that still runs the real
    enterprise path (enrollment, upload, worker, portal)."""
    from sqlalchemy import func, select

    from bix.device import config as dcfg
    from bix.server.db import User

    srv = _server(args)
    with srv.db.session() as s:
        has_users = s.scalar(select(func.count()).select_from(User)) > 0
    if not has_users:
        email = args.admin_email or input("Имейл на администратора: ").strip()
        password = secrets.token_urlsafe(12)
        _create_admin(srv, email, org_name="Моята организация", password=password)
        cred = srv.cfg.dir / "first-admin.txt"
        cred.write_text(f"{email}\n{password}\n", encoding="utf-8")
        print(f"\n  Вход в портала: {email} / {password}\n  (записано и в {cred}; сменете паролата от портала)\n")

    threading.Thread(target=_serve, args=(srv,), name="bix-server", daemon=True).start()
    url = f"http://{srv.cfg.http.host}:{srv.cfg.http.port}"
    _wait_http(url + "/healthz")

    dev_cfg, _ = dcfg.load(None)
    dev_cfg.panel.enabled = True
    from bix.device.client import ServerClient

    client = ServerClient(dev_cfg)
    if not client.enrolled:
        import platform

        code = _enrollment_code(srv, f"Лаптоп · {platform.node()}", hours=1)
        client.enroll(url, code)
        log.info("device enrolled with the local server")
    if not args.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
        threading.Timer(2.5, lambda: webbrowser.open(f"http://{dev_cfg.panel.host}:{dev_cfg.panel.port}/sim")).start()
    panel = f"http://{dev_cfg.panel.host}:{dev_cfg.panel.port}"
    print(f"  Портал: {url}\n  Екранът в залата: {panel}\n  Виртуално устройство (3D, радар, Mute): {panel}/sim\n  Спиране: Ctrl+C\n")
    _run_device(dev_cfg, face=args.face, open_browser=False)


def _wait_http(url: str, timeout: float = 30) -> None:
    import httpx

    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise SystemExit(f"Сървърът не тръгна: {url}")


# --- doctor & eval -----------------------------------------------------------------


def cmd_doctor(args) -> None:
    from bix.common import models
    from bix.common.settings import secret

    srv = _server(args)
    cfg = srv.cfg
    ok = True

    def line(good: bool, what: str, detail: str = "", required: bool = True) -> None:
        nonlocal ok
        ok &= good or not required
        mark = "OK" if good else ("!!" if required else "--")
        print(f"  [{mark}] {what}{(': ' + detail) if detail else ''}")

    print(f"Сървър: {srv.cfg_path}")
    line(True, "база данни", cfg.db_url.split("@")[-1])
    line(True, "хранилище", cfg.storage.backend + " " + (cfg.storage.s3_bucket or str(cfg.storage_dir)))
    try:
        from bix.server import stt

        line(True, f"реч → текст ({cfg.stt.provider})", str(stt.make(cfg).check()))
    except Exception as e:
        line(False, f"реч → текст ({cfg.stt.provider})", str(e))
    llm = cfg.llm
    if llm.provider == "anthropic":
        line(bool(secret(llm.api_key, llm.api_key_env)), f"минути (Claude API, {llm.model})", "" if secret(llm.api_key, llm.api_key_env) else f"липсва {llm.api_key_env}")
    elif llm.provider == "claude_cli":
        import shutil

        line(bool(shutil.which(llm.claude_cli_path)), "минути (Claude Code CLI)", llm.claude_cli_path)
    elif llm.provider == "vllm":
        from bix.server.minutes import vllm_model

        try:
            line(True, "минути (vLLM)", f"{vllm_model(llm, probe=True)} @ {llm.vllm_url}")
        except Exception as e:
            line(False, "минути (vLLM)", str(e))
    else:
        line(llm.provider != "none", f"минути ({llm.provider})", llm.model)
    try:
        models.ensure(cfg.models_dir, cfg.diarization.embedding_model, progress=lambda m: print("   ", m))
        line(True, "модели за говорещи", str(cfg.models_dir))
    except Exception as e:
        line(False, "модели за говорещи", str(e))
    line(bool(cfg.smtp.host) or srv.graph.enabled, "имейл", cfg.smtp.host or ("Microsoft Graph" if srv.graph.enabled else "не е настроен (минутите се виждат в портала)"), required=False)
    try:
        from bix.device.capture import list_inputs

        mics = list_inputs()
        # only devices need a microphone; on a server this is information, not a failure
        line(bool(mics), "микрофони (само за устройство)", ", ".join(d["name"] for d in mics[:3]) or "няма", required=False)
    except Exception as e:
        line(False, "микрофони (само за устройство)", str(e), required=False)
    sys.exit(0 if ok else 1)


def cmd_eval(args) -> None:
    import json
    from pathlib import Path

    from bix.common.wer import cer, wer

    ref = Path(args.reference).read_text(encoding="utf-8")
    hyp_raw = Path(args.hypothesis).read_text(encoding="utf-8")
    hyp = " ".join(s["text"] for s in json.loads(hyp_raw)["segments"]) if args.hypothesis.endswith(".json") else hyp_raw
    print(f"WER {wer(ref, hyp):.1%}   CER {cer(ref, hyp):.1%}")


if __name__ == "__main__":
    main()
