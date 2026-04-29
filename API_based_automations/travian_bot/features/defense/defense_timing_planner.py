import math
from datetime import datetime, timedelta

from identity_handling.identity_helper import load_villages_from_identity


def _distance(x1: int, y1: int, x2: int, y2: int) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def _parse_time_hms(value: str) -> tuple[int, int, int]:
    raw = (value or "").strip()
    parts = raw.split(":")
    if len(parts) != 3:
        raise ValueError("time must be HH:MM:SS")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        raise ValueError("invalid HH:MM:SS range")
    return h, m, s


def _parse_attack_arrivals(raw: str, server_now: datetime) -> list[datetime]:
    tokens = [x.strip() for x in (raw or "").split(",") if x.strip()]
    if not tokens:
        return []

    arrivals = []
    for token in tokens:
        h, m, s = _parse_time_hms(token)
        candidate = server_now.replace(hour=h, minute=m, second=s, microsecond=0)
        # If arrival time already passed, treat it as next-day arrival.
        if candidate < server_now:
            candidate = candidate + timedelta(days=1)
        arrivals.append(candidate)
    arrivals.sort()
    return arrivals


def _print_villages(villages: list[dict]) -> None:
    print("\nKnown villages from identity:")
    for idx, village in enumerate(villages, start=1):
        print(
            f"[{idx}] {village.get('village_name', f'village_{idx}')} "
            f"(id={village.get('village_id', '?')}) "
            f"at ({village.get('x', '?')}|{village.get('y', '?')})"
        )


def _pick_target(villages: list[dict]) -> dict:
    while True:
        target_in = input("Target village index under attack: ").strip()
        try:
            idx = int(target_in)
            if 1 <= idx <= len(villages):
                return villages[idx - 1]
        except ValueError:
            pass
        print("Invalid target village index.")


def _pick_senders(villages: list[dict], target: dict) -> list[dict]:
    default_indexes = [
        str(i) for i, v in enumerate(villages, start=1) if int(v.get("village_id", -1)) != int(target.get("village_id", -2))
    ]
    default_hint = ",".join(default_indexes) if default_indexes else ""
    raw = input(
        f"Sender village indexes (comma-separated, blank={default_hint or 'none'}): "
    ).strip()
    selected_tokens = [x.strip() for x in raw.split(",") if x.strip()] if raw else default_indexes
    out = []
    seen = set()
    for tok in selected_tokens:
        try:
            idx = int(tok)
            if 1 <= idx <= len(villages):
                village = villages[idx - 1]
                vid = int(village.get("village_id", -1))
                if vid == int(target.get("village_id", -2)):
                    continue
                if vid in seen:
                    continue
                seen.add(vid)
                out.append(village)
        except ValueError:
            continue
    return out


def _fmt(dt_obj: datetime) -> str:
    return dt_obj.strftime("%Y-%m-%d %H:%M:%S")


def _prompt_manual_senders() -> list[dict]:
    print("\nManual sender input")
    print("Format: name:x:y,name2:x:y")
    raw = input("Enter manual sender villages: ").strip()
    if not raw:
        return []
    out = []
    for token in [x.strip() for x in raw.split(",") if x.strip()]:
        parts = token.split(":")
        if len(parts) != 3:
            continue
        name = parts[0].strip() or "manual_sender"
        try:
            x = int(parts[1].strip())
            y = int(parts[2].strip())
        except ValueError:
            continue
        out.append({"village_name": name, "village_id": -1, "x": x, "y": y})
    return out


def run_defense_timing_planner() -> None:
    print("\nDefense Timing Planner")
    print("Plan reinforcement sends to land between incoming attacks.")

    try:
        villages = load_villages_from_identity()
    except Exception as e:
        print(f"Could not load villages from identity: {e}")
        return

    if not villages:
        print("No villages found in identity.")
        return

    _print_villages(villages)
    target = _pick_target(villages)
    senders = _pick_senders(villages, target)
    if not senders:
        print("No valid sender villages selected from identity.")
        senders = _prompt_manual_senders()
        if not senders:
            print("No sender villages available.")
            return

    # Keep server-time arithmetic anchored on today's local date.
    now_local = datetime.now().replace(microsecond=0)
    server_now_raw = input(
        f"Server time now HH:MM:SS (blank uses local {now_local.strftime('%H:%M:%S')}): "
    ).strip()
    if server_now_raw:
        try:
            h, m, s = _parse_time_hms(server_now_raw)
            server_now = now_local.replace(hour=h, minute=m, second=s)
        except ValueError as e:
            print(f"Invalid server time: {e}")
            return
    else:
        server_now = now_local

    incoming_raw = input(
        "Incoming attack arrival times HH:MM:SS comma-separated (example: 22:57:17,22:57:18): "
    ).strip()
    arrivals = _parse_attack_arrivals(incoming_raw, server_now)
    if len(arrivals) < 2:
        print("At least two incoming arrivals are required to plan a between-waves landing.")
        return

    print("\nIncoming waves:")
    for idx, arr in enumerate(arrivals, start=1):
        print(f"[{idx}] {_fmt(arr)}")

    print("\nDefault mode: let the FIRST wave hit, then defend the NEXT waves.")
    start_idx_raw = input("Window starts AFTER which wave index? [default 1]: ").strip()
    end_idx_raw = input(
        f"Window ends BEFORE which wave index? [default {len(arrivals)}]: "
    ).strip()
    try:
        start_idx = int(start_idx_raw) if start_idx_raw else 1
        end_idx = int(end_idx_raw) if end_idx_raw else len(arrivals)
    except ValueError:
        print("Wave indexes must be integers.")
        return
    if not (1 <= start_idx < end_idx <= len(arrivals)):
        print("Invalid wave index range.")
        return

    speed_in = input("Defense unit speed (fields/hour, Phalanx=7) [default 7]: ").strip()
    try:
        speed = float(speed_in) if speed_in else 7.0
    except ValueError:
        print("Invalid speed.")
        return
    if speed <= 0:
        print("Speed must be > 0.")
        return

    server_speed_in = input("Server speed multiplier xN [default 1]: ").strip()
    try:
        server_speed = float(server_speed_in) if server_speed_in else 1.0
    except ValueError:
        print("Invalid server speed multiplier.")
        return
    if server_speed <= 0:
        print("Server speed multiplier must be > 0.")
        return

    after_first_in = input("Land this many seconds AFTER first attack [default 8]: ").strip()
    before_second_in = input("Keep this many seconds BEFORE second attack [default 2]: ").strip()
    try:
        after_first_s = int(after_first_in) if after_first_in else 8
        before_second_s = int(before_second_in) if before_second_in else 2
    except ValueError:
        print("Offsets must be integers.")
        return

    first = arrivals[start_idx - 1]
    second = arrivals[end_idx - 1]
    window_start = first + timedelta(seconds=after_first_s)
    window_end = second - timedelta(seconds=before_second_s)

    if window_end <= window_start:
        print("\nNo usable between-wave window with current offsets.")
        print(f"First attack:  {_fmt(first)}")
        print(f"Second attack: {_fmt(second)}")
        print("Try smaller 'after first' and/or smaller 'before second' values.")
        return

    # Choose a stable landing point in the middle of the valid window.
    landing = window_start + (window_end - window_start) / 2
    print("\nPlanned defense landing window:")
    print(f"- first attack:  {_fmt(first)}")
    print(f"- second attack: {_fmt(second)}")
    print(f"- window start:  {_fmt(window_start)}")
    print(f"- window end:    {_fmt(window_end)}")
    print(f"- target land:   {_fmt(landing)}")

    print("\nSend plan:")
    for sender in senders:
        sx, sy = int(sender.get("x", 0)), int(sender.get("y", 0))
        tx, ty = int(target.get("x", 0)), int(target.get("y", 0))
        dist = _distance(sx, sy, tx, ty)
        travel_seconds = (dist / (speed * server_speed)) * 3600.0
        send_at = landing - timedelta(seconds=travel_seconds)
        send_in = (send_at - server_now).total_seconds()
        status = "SEND NOW" if send_in <= 0 else f"send in {int(send_in)}s"
        print(
            f"- {sender.get('village_name', '?')} ({sx}|{sy}) -> ({tx}|{ty}) "
            f"dist={dist:.2f} travel={int(travel_seconds)}s send_at={_fmt(send_at)} [{status}]"
        )
