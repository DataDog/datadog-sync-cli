# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Pre-flight check (Opção A) — verificação read-only das configurações NÃO-migráveis
antes de rodar uma migração com datadog-sync-cli.

Este script é INTENCIONALMENTE isolado do fluxo de migração:

  * não importa nem toca em ResourcesHandler / commands / state;
  * reusa apenas CustomClient como biblioteca de HTTP;
  * executa SOMENTE requisições GET — jamais POST/PUT/PATCH/DELETE;
  * não lê nem grava o `state` em disco (a única saída é o relatório).

Portanto, rodá-lo não altera nem a org de origem nem a de destino, e não pode
interferir num `sync`/`import`/`migrate`.

O que ele compara (apenas o que a migração NÃO cobre):

  1. Handles de notificação (@slack-…, @pagerduty-…, @webhook-…, @team-…) que os
     monitores da ORIGEM referenciam no campo `message`. Esses handles NÃO são
     remapeados pela migração; se a integração correspondente não existir no
     destino, o monitor pode falhar (400) ou simplesmente não notificar.
  2. Integrações instaladas no DESTINO (cloud AWS/GCP/Azure, PagerDuty, webhooks,
     Slack) — enumeradas em best-effort; onde a API permite, calcula-se o gap.
  3. Private Locations — contagem/identidade origem×destino (sanity check padrão).
     Observação: isso cobre a DEFINIÇÃO da PL, não o worker (que roda na sua infra
     e continua sendo verificação manual).

Uso:

    export DD_SOURCE_API_KEY=...        DD_SOURCE_APP_KEY=...
    export DD_DESTINATION_API_KEY=...   DD_DESTINATION_APP_KEY=...
    # opcional (default https://api.datadoghq.com):
    export DD_SOURCE_API_URL=...        DD_DESTINATION_API_URL=...

    python scripts/preflight_check.py                 # relatório em texto
    python scripts/preflight_check.py --report-format json
    python scripts/preflight_check.py --no-fail-on-gap

Exit code: 1 se houver gaps (a menos que --no-fail-on-gap), 0 caso contrário.
Erros de execução (auth, rede) → exit code 2.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Reusa o cliente HTTP e a config de paginação do próprio projeto. Nada além disto
# é importado do datadog_sync — sem ResourcesHandler, sem commands, sem state.
from datadog_sync.utils.custom_client import CustomClient, PaginationConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError

DEFAULT_API_URL = "https://api.datadoghq.com"

# Paginação equivalente à usada por Monitors (datadog_sync/model/monitors.py):
# GET /api/v1/monitor retorna uma LISTA simples (sem accessor), page/page_size.
_MONITORS_PAGINATION = PaginationConfig()
_MONITORS_PAGINATION.page_size = 1000
_MONITORS_PAGINATION.page_number_param = "page"
_MONITORS_PAGINATION.page_size_param = "page_size"
_MONITORS_PAGINATION.remaining_func = lambda *args: 1
_MONITORS_PAGINATION.response_list_accessor = None

# Extrai @handles de um texto de notificação de monitor. Aceita a forma comum
# `@slack-canal`, `@pagerduty-Servico`, `@webhook-Nome`, `@team-x` e menções de
# e-mail `@user@dominio.com`. Não captura template vars como {{#is_alert}}.
_HANDLE_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9._\-]*(?:@[A-Za-z0-9._\-]+)?)")


def _classify_handle(handle: str) -> str:
    """Classifica um handle de notificação pelo seu prefixo/forma."""
    h = handle.lower()
    if h.startswith("slack-"):
        return "slack"
    if h.startswith("pagerduty"):
        return "pagerduty"
    if h.startswith("webhook-"):
        return "webhook"
    if h.startswith("opsgenie"):
        return "opsgenie"
    if h.startswith("teams-") or h.startswith("microsoft-teams"):
        return "teams"
    if "@" in handle:
        return "email"
    return "other"


@dataclass
class HandleRef:
    handle: str
    category: str
    monitor_count: int


@dataclass
class CategoryGap:
    category: str
    referenced: List[HandleRef] = field(default_factory=list)
    installed: Optional[List[str]] = None  # None = não verificável via API
    missing: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class PLCount:
    source: int
    destination: int
    source_ids: List[str] = field(default_factory=list)
    destination_ids: List[str] = field(default_factory=list)
    missing_in_destination: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Construção dos clientes (read-only)
# ---------------------------------------------------------------------------


def _build_auth(api_key: Optional[str], app_key: Optional[str], jwt: Optional[str]) -> Dict[str, str]:
    """Monta o dict de auth no mesmo formato de configuration.build_config.

    JWT tem precedência sobre API key (espelha o comportamento do projeto).
    """
    auth: Dict[str, str] = {}
    if jwt:
        auth["jwtAuth"] = jwt
    elif api_key:
        auth["apiKeyAuth"] = api_key
        if app_key:
            auth["appKeyAuth"] = app_key
    return auth


def _make_client(url: str, auth: Dict[str, str], retry_timeout: int, timeout: int) -> CustomClient:
    # send_metrics=False de propósito: um pre-check não deve emitir métricas.
    return CustomClient(url, auth, retry_timeout, timeout, False)


@dataclass
class Endpoints:
    """Origem e destino resolvidos a partir de env/args."""

    source: CustomClient
    destination: CustomClient


def _resolve_clients(args: argparse.Namespace) -> Endpoints:
    source_auth = _build_auth(
        os.getenv("DD_SOURCE_API_KEY"),
        os.getenv("DD_SOURCE_APP_KEY"),
        os.getenv("DD_SOURCE_JWT"),
    )
    dest_auth = _build_auth(
        os.getenv("DD_DESTINATION_API_KEY"),
        os.getenv("DD_DESTINATION_APP_KEY"),
        os.getenv("DD_DESTINATION_JWT"),
    )
    if not source_auth:
        raise RuntimeError("Credenciais de ORIGEM ausentes (defina DD_SOURCE_API_KEY[/APP_KEY] ou DD_SOURCE_JWT).")
    if not dest_auth:
        raise RuntimeError(
            "Credenciais de DESTINO ausentes (defina DD_DESTINATION_API_KEY[/APP_KEY] ou DD_DESTINATION_JWT)."
        )

    source_url = os.getenv("DD_SOURCE_API_URL") or DEFAULT_API_URL
    dest_url = os.getenv("DD_DESTINATION_API_URL") or DEFAULT_API_URL

    return Endpoints(
        source=_make_client(source_url, source_auth, args.http_client_retry_timeout, args.http_client_timeout),
        destination=_make_client(dest_url, dest_auth, args.http_client_retry_timeout, args.http_client_timeout),
    )


# ---------------------------------------------------------------------------
# Fase 1 — coleta de referências (só na ORIGEM)
# ---------------------------------------------------------------------------


async def _get_source_monitors(client: CustomClient) -> List[Dict]:
    return await client.paginated_request(client.get)(
        "/api/v1/monitor", pagination_config=_MONITORS_PAGINATION
    )


def _extract_handles(monitors: List[Dict]) -> List[HandleRef]:
    """Extrai handles distintos do campo `message` de cada monitor, com contagem
    de quantos monitores referenciam cada handle."""
    counts: Dict[str, int] = defaultdict(int)
    for mon in monitors:
        message = mon.get("message")
        if not isinstance(message, str):
            continue
        seen_in_monitor = set(_HANDLE_RE.findall(message))
        for handle in seen_in_monitor:
            counts[handle] += 1
    refs = [HandleRef(handle=h, category=_classify_handle(h), monitor_count=n) for h, n in counts.items()]
    refs.sort(key=lambda r: (r.category, -r.monitor_count, r.handle))
    return refs


# ---------------------------------------------------------------------------
# Fase 2 — enumeração de integrações no DESTINO (best-effort, só GET)
# ---------------------------------------------------------------------------


async def _safe_get(client: CustomClient, path: str, **kwargs) -> Tuple[Optional[object], Optional[str]]:
    """GET tolerante a falha: retorna (data, None) em sucesso ou (None, motivo).

    Nunca levanta — um endpoint indisponível/deprecado (404/403) apenas torna
    aquela verificação "não verificável", sem abortar o pre-check inteiro.
    """
    try:
        return await client.get(path, **kwargs), None
    except CustomClientHTTPError as e:
        return None, f"HTTP {e.status_code}"
    except Exception as e:  # rede, timeout, parsing
        return None, str(e)[:120]


async def _installed_pagerduty(client: CustomClient) -> Tuple[Optional[List[str]], str]:
    data, err = await _safe_get(client, "/api/v1/integration/pagerduty")
    if err:
        return None, f"não verificável ({err})"
    services = (data or {}).get("services", []) if isinstance(data, dict) else []
    names = [s.get("service_name", "") for s in services if isinstance(s, dict)]
    # handles pagerduty são referenciados como @pagerduty-<service_name>
    return [f"pagerduty-{n}" for n in names if n], ""


async def _installed_webhooks(client: CustomClient) -> Tuple[Optional[List[str]], str]:
    data, err = await _safe_get(client, "/api/v1/integration/webhooks/configuration/webhooks")
    if err:
        return None, f"não verificável ({err})"
    items = data if isinstance(data, list) else (data or {}).get("webhooks", [])
    names = [w.get("name", "") for w in items if isinstance(w, dict)]
    return [f"webhook-{n}" for n in names if n], ""


async def _installed_slack(client: CustomClient) -> Tuple[Optional[List[str]], str]:
    # O endpoint de Slack varia por org/versão e frequentemente exige o nome da
    # conta; tratamos como best-effort e, na dúvida, "não verificável".
    data, err = await _safe_get(client, "/api/v1/integration/slack/channels")
    if err:
        return None, f"não verificável ({err}) — verifique manualmente no destino"
    if not isinstance(data, list):
        return None, "não verificável (formato inesperado) — verifique manualmente no destino"
    names = [c.get("name", "") for c in data if isinstance(c, dict)]
    return [f"slack-{n.lstrip('#')}" for n in names if n], ""


async def _cloud_accounts(client: CustomClient, provider: str, path: str, accessor: str) -> Tuple[Optional[int], str]:
    data, err = await _safe_get(client, path)
    if err:
        return None, f"não verificável ({err})"
    if isinstance(data, dict):
        items = data.get(accessor, [])
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return len(items), ""


# ---------------------------------------------------------------------------
# Fase 3 — contagem de Private Locations (origem e destino)
# ---------------------------------------------------------------------------

_PL_ID_RE = re.compile(r"^pl:")


async def _private_locations(client: CustomClient) -> List[str]:
    """Retorna os ids das Private Locations (id começa com 'pl:').

    Usa /api/v1/synthetics/locations (mesmo endpoint do model do projeto), que
    retorna {"locations": [...]} com PLs e managed locations misturadas.
    """
    data, err = await _safe_get(client, "/api/v1/synthetics/locations")
    if err or not isinstance(data, dict):
        return []
    locations = data.get("locations", [])
    return [loc.get("id", "") for loc in locations if isinstance(loc, dict) and _PL_ID_RE.match(loc.get("id", ""))]


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------


async def _run(endpoints: Endpoints) -> Tuple[List[CategoryGap], PLCount, List[HandleRef]]:
    src, dst = endpoints.source, endpoints.destination

    # Fase 1: origem
    monitors = await _get_source_monitors(src)
    refs = _extract_handles(monitors)
    refs_by_cat: Dict[str, List[HandleRef]] = defaultdict(list)
    for r in refs:
        refs_by_cat[r.category].append(r)

    # Fase 2: destino (integrações)
    gaps: List[CategoryGap] = []

    pd_installed, pd_note = await _installed_pagerduty(dst)
    wh_installed, wh_note = await _installed_webhooks(dst)
    sl_installed, sl_note = await _installed_slack(dst)

    installed_map = {
        "pagerduty": (pd_installed, pd_note),
        "webhook": (wh_installed, wh_note),
        "slack": (sl_installed, sl_note),
    }

    # Categorias que têm handles referenciados + as verificáveis por integração
    all_cats = set(refs_by_cat) | set(installed_map)
    for cat in sorted(all_cats):
        referenced = refs_by_cat.get(cat, [])
        installed, note = installed_map.get(cat, (None, ""))
        gap = CategoryGap(category=cat, referenced=referenced, installed=installed, note=note)
        if installed is not None:
            installed_set = {i.lower() for i in installed}
            gap.missing = [r.handle for r in referenced if r.handle.lower() not in installed_set]
        gaps.append(gap)

    # Fase 2b: cloud integrations (contagem de contas no destino)
    cloud_gaps: List[CategoryGap] = []
    for provider, path, accessor in (
        ("aws", "/api/v1/integration/aws", "accounts"),
        ("gcp", "/api/v1/integration/gcp", "projects"),
        ("azure", "/api/v1/integration/azure", "accounts"),
    ):
        count, note = await _cloud_accounts(dst, provider, path, accessor)
        cg = CategoryGap(category=f"cloud/{provider}", installed=None if count is None else [], note=note)
        cg.note = note or (f"{count} conta(s) configurada(s) no destino" + (" ⚠️ nenhuma" if count == 0 else ""))
        cloud_gaps.append(cg)
    gaps.extend(cloud_gaps)

    # Fase 3: private locations
    src_pls = await _private_locations(src)
    dst_pls = await _private_locations(dst)
    dst_set = set(dst_pls)
    pl = PLCount(
        source=len(src_pls),
        destination=len(dst_pls),
        source_ids=sorted(src_pls),
        destination_ids=sorted(dst_pls),
        missing_in_destination=sorted([p for p in src_pls if p not in dst_set]),
    )

    return gaps, pl, refs


def _has_gaps(gaps: List[CategoryGap], pl: PLCount) -> bool:
    if any(g.missing for g in gaps):
        return True
    if pl.missing_in_destination:
        return True
    return False


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------


def _render_text(gaps: List[CategoryGap], pl: PLCount) -> str:
    lines = ["", "PREFLIGHT — configs não-migráveis (origem → destino)", ""]

    notif_cats = [g for g in gaps if not g.category.startswith("cloud/")]
    for g in notif_cats:
        ref_total = len(g.referenced)
        header = f"[{g.category.upper()}]"
        if g.installed is None:
            if ref_total:
                lines.append(f"{header}  {ref_total} handle(s) referenciado(s) — {g.note or 'não verificável'}")
                for r in g.referenced:
                    lines.append(f"    @{r.handle}   (usado por {r.monitor_count} monitor(es))")
            elif g.note:
                lines.append(f"{header}  {g.note}")
        else:
            if g.missing:
                lines.append(f"{header}  {len(g.missing)} gap(s)")
                miss_counts = {r.handle: r.monitor_count for r in g.referenced}
                for h in g.missing:
                    lines.append(f"    @{h}   ← faltando no destino (usado por {miss_counts.get(h, 0)} monitor(es))")
            else:
                lines.append(f"{header}  OK ({ref_total} referenciado(s), todos presentes)")

    for g in gaps:
        if g.category.startswith("cloud/"):
            lines.append(f"[{g.category.upper()}]  {g.note}")

    marker = "⚠️" if pl.missing_in_destination else "OK"
    lines.append(
        f"[PRIVATE LOCATIONS]  contagem: origem={pl.source} destino={pl.destination}"
        f"  → {len(pl.missing_in_destination)} ausente(s) no destino  ({marker})"
    )
    for pid in pl.missing_in_destination:
        lines.append(f"    {pid}   ← definição de PL ausente no destino")
    if pl.source or pl.destination:
        lines.append("    (contagem cobre a DEFINIÇÃO da PL; o worker na sua infra é verificação manual)")

    total_gaps = sum(len(g.missing) for g in gaps) + len(pl.missing_in_destination)
    lines.append("")
    lines.append(f"Resultado: {total_gaps} gap(s) verificável(is) encontrado(s)")
    lines.append("")
    return "\n".join(lines)


def _render_json(gaps: List[CategoryGap], pl: PLCount) -> str:
    payload = {
        "categories": [
            {
                "category": g.category,
                "referenced": [
                    {"handle": r.handle, "monitor_count": r.monitor_count} for r in g.referenced
                ],
                "installed": g.installed,
                "missing": g.missing,
                "note": g.note,
            }
            for g in gaps
        ],
        "private_locations": {
            "source": pl.source,
            "destination": pl.destination,
            "missing_in_destination": pl.missing_in_destination,
        },
        "total_verifiable_gaps": sum(len(g.missing) for g in gaps) + len(pl.missing_in_destination),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-flight check read-only das configs não-migráveis (datadog-sync-cli).",
    )
    parser.add_argument(
        "--report-format",
        choices=("text", "json"),
        default="text",
        help="Formato do relatório (default: text).",
    )
    parser.add_argument(
        "--no-fail-on-gap",
        dest="fail_on_gap",
        action="store_false",
        help="Não retornar exit code 1 quando houver gaps (default: falha em gap).",
    )
    parser.set_defaults(fail_on_gap=True)
    parser.add_argument("--http-client-retry-timeout", type=int, default=60)
    parser.add_argument("--http-client-timeout", type=int, default=30)
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> int:
    endpoints = _resolve_clients(args)
    await endpoints.source._init_session()
    await endpoints.destination._init_session()
    try:
        gaps, pl, _refs = await _run(endpoints)
    finally:
        await endpoints.source._end_session()
        await endpoints.destination._end_session()

    if args.report_format == "json":
        print(_render_json(gaps, pl))
    else:
        print(_render_text(gaps, pl))

    if args.fail_on_gap and _has_gaps(gaps, pl):
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_main_async(args))
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("Interrompido pelo usuário.", file=sys.stderr)
        return 2
    except Exception as e:  # falha de execução (auth/rede) — não é "gap"
        print(f"Erro ao executar o pre-flight check: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
