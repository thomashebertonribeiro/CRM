"""
Prospector API — Pipeline Service

Business logic for each pipeline step (Discovery, Enrich, Score, Analyze).
Follows CORE-03 (controller → service → model) architecture.
"""
import uuid
import time
import threading
from difflib import SequenceMatcher

from app.config.settings import (
    BLACKLISTED_DOMAINS, BLACKLISTED_TITLE_WORDS, DEFAULT_MAX_RESULTS,
    MAX_QUERY_VARIATIONS, CNPJ_API_URL,
)
from app.services.persistence import load_search, save_search, set_status
from app.services.external_api import serper_search, check_cnpj, ai_analyze, parse_json_from_ia
from app.services.scraper import (
    fetch_site_data, try_cnpj_from_phone, extract_cnpj, get_domain,
)


# ─── Fuzzy similarity ───

def fuzzy_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ─── Completeness score for deduplication ───

def completeness_score(lead: dict) -> int:
    score = 0
    if lead.get("title"): score += 1
    if lead.get("snippet"): score += 1
    if lead.get("tem_site"): score += 2
    if lead.get("tem_instagram"): score += 1
    if lead.get("tem_maps"): score += 2
    if lead.get("maps_rating"): score += 1
    if lead.get("cnpj"): score += 3
    if lead.get("site_url"): score += 1
    if lead.get("instagram_url"): score += 1
    if lead.get("site_emails"): score += 2
    if lead.get("site_phones"): score += 1
    if lead.get("site_youtube"): score += 1
    if lead.get("site_tiktok"): score += 1
    return score


def _merge_lead(target: dict, source: dict) -> None:
    """Merge source lead data into target (only fill missing fields)."""
    for key, value in source.items():
        if key in ("id", "search_id"):
            continue
        if value and not target.get(key):
            target[key] = value
        if key.startswith("tem_") and isinstance(value, bool) and value:
            target[key] = True


# ─── Spam filter ───

def is_spam_result(result: dict) -> bool:
    title = (result.get("title") or "").lower().strip()
    link = (result.get("link") or "").lower().strip()
    domain = get_domain(link)

    for bd in BLACKLISTED_DOMAINS:
        if domain == bd or domain.endswith("." + bd):
            return True
    for word in BLACKLISTED_TITLE_WORDS:
        if word in title:
            return True
    if len(title) < 3:
        return True
    return False


# ─── Query variation generator ───

def generate_query_variations(niche: str, city: str, state: str) -> list[str]:
    niche_lower = niche.lower().strip()
    city_state = f"{city} {state}".strip()
    variations = [f"{niche} {city_state}"]

    niche_words = niche_lower.split()
    core_terms = [w for w in niche_words if w not in ("de", "da", "do", "das", "dos", "em", "para", "com")]

    if state:
        variations.append(f"{niche} {city} {state}")
    if len(niche_words) >= 2 and core_terms:
        variations.append(f"{' '.join(core_terms)} {city}")
        if state:
            variations.append(f"{' '.join(core_terms)} {city} {state}")

    niche_specific = {
        "estética": [f"estética facial {city}", f"estética corporal {city}", f"harmonização facial {city}", f"clínica beleza {city}", f"tratamento estético {city}"],
        "odontologia": [f"consultório dentista {city}", f"implante dentário {city}", f"clínica dentária {city}", f"ortodontia {city}"],
        "advogado": [f"escritório advocacia {city}", f"advocacia {city}", f"advogado trabalhista {city}", f"advogado civil {city}"],
        "academia": [f"academia {city}", f"crossfit {city}", f"musculação {city}", f"personal trainer {city}"],
        "restaurante": [f"restaurante {city}", f"gastronomia {city}", f"pizzaria {city}", f"lanchonete {city}"],
        "farmácia": [f"farmácia {city}", f"drogaria {city}", f"farmácia de manipulação {city}"],
        "pet shop": [f"pet shop {city}", f"petshop {city}", f"veterinário {city}", f"clínica veterinária {city}"],
        "imobiliária": [f"imobiliária {city}", f"corretor imóveis {city}", f"compra venda imóveis {city}"],
    }

    for key, extra_variations in niche_specific.items():
        if key in niche_lower:
            for v in extra_variations:
                if v not in variations:
                    variations.append(v)
            break
    else:
        variations.append(f"{niche_lower} em {city}")
        if len(niche_words) >= 2:
            last_words = " ".join(niche_words[-2:])
            variations.append(f"{last_words} {city}")
        variations.append(f"{niche_lower} {city} endereço")

    seen = set()
    unique = []
    for v in variations:
        v_lower = v.lower().strip()
        if v_lower not in seen:
            seen.add(v_lower)
            unique.append(v)
    return unique[:MAX_QUERY_VARIATIONS]


# ─── Score calculator ───

def calculate_score(lead: dict) -> int:
    score = 0
    cap = lead.get("capital_social", 0) or 0
    if isinstance(cap, str):
        try:
            cap = float(cap)
        except ValueError:
            cap = 0
    if cap >= 100000: score += 20
    elif cap >= 50000: score += 15
    elif cap >= 10000: score += 10
    elif cap >= 1000: score += 5

    if lead.get("tem_site"): score += 10
    if lead.get("tem_instagram"): score += 10
    if lead.get("tem_maps"): score += 10
    if lead.get("tem_ads"): score += 10
    if lead.get("site_emails"): score += 5
    if lead.get("site_phones"): score += 3
    if lead.get("site_youtube"): score += 3
    if lead.get("site_tiktok"): score += 2

    inicio = lead.get("data_inicio", "")
    if inicio and len(inicio) >= 4:
        try:
            anos = 2026 - int(inicio[:4])
            if anos >= 5: score += 15
            elif anos >= 3: score += 10
            elif anos >= 1: score += 5
        except ValueError:
            pass

    rating = lead.get("maps_rating", 0) or 0
    if isinstance(rating, str):
        try:
            rating = float(rating)
        except ValueError:
            rating = 0
    if rating >= 4.5: score += 15
    elif rating >= 4.0: score += 10
    elif rating >= 3.5: score += 5

    if lead.get("opcao_pelo_mei"): score -= 5

    return max(0, min(100, score))


# ─── Deduplication ───

def _deduplicate_leads(leads: list[dict]) -> list[dict]:
    if not leads:
        return leads
    result = []
    seen_domains = {}
    seen_cnpjs = set()

    for lead in leads:
        title = (lead.get("title") or "").strip()
        domain = get_domain(lead.get("link", ""))
        cnpj = lead.get("cnpj") or lead.get("cnpj_hint")
        is_dup = False

        if domain and domain in seen_domains:
            existing_idx = seen_domains[domain]
            existing = result[existing_idx]
            if completeness_score(lead) > completeness_score(existing):
                _merge_lead(existing, lead)
            is_dup = True

        if cnpj and len(cnpj) >= 14:
            if cnpj in seen_cnpjs:
                is_dup = True
            else:
                seen_cnpjs.add(cnpj)

        if not is_dup:
            for existing in result:
                existing_title = existing.get("title", "")
                
                # Do not fuzzy-match generic CNPJ fallback titles
                if title.startswith("Empresa CNPJ") and existing_title.startswith("Empresa CNPJ"):
                    continue
                    
                sim = fuzzy_similarity(title, existing_title)
                if sim >= 0.7:
                    if completeness_score(lead) > completeness_score(existing):
                        _merge_lead(existing, lead)
                    is_dup = True
                    break

        if not is_dup:
            seen_domains[domain] = len(result)
            result.append(lead)

    return result


def _get_total_to_analyze(leads: list[dict]) -> int:
    if len(leads) >= 30:
        return min(20, len(leads))
    return len(leads)


# ─── STEP 1: Discovery ───

def run_discovery(niche: str, city: str, state: str, max_results: int = DEFAULT_MAX_RESULTS, search_id: str = None) -> dict:
    if search_id is None:
        search_id = str(uuid.uuid4())[:8]
    base_query = f"{niche} {city} {state}"

    query_variations = generate_query_variations(niche, city, state)
    print(f"[{search_id}] Query variations: {query_variations}")

    queries_total = len(query_variations) + 1
    queries_done = 0
    summary_progress = {
        "queries_total": queries_total,
        "queries_done": 0,
        "status": "discovering",
        "total_results": 0,
        "com_site": 0,
        "sem_site": 0,
        "pct_sem_site": 0,
        "com_instagram": 0,
        "com_ads": 0,
        "com_maps": 0,
        "com_cnpj": 0,
    }

    # Places search
    print(f"[{search_id}] Busca 1/{queries_total}: Places — {base_query}")
    places_data = serper_search(base_query, "places")
    queries_done += 1
    summary_progress["queries_done"] = queries_done
    summary_progress["current_query"] = f"Places: {base_query}"
    places = places_data.get("places", [])

    save_search(search_id, {
        "status": "discovering",
        "summary": {
            "search_id": search_id, "niche": niche, "city": city, "state": state,
            "query": base_query, "query_variations": query_variations,
            **summary_progress,
        },
        "leads": [],
    })

    # Build places lookup
    places_map = {}
    for p in places:
        name = p.get("title", "").lower()
        places_map[name] = {
            "maps_title": p.get("title"), "maps_rating": p.get("rating"),
            "maps_reviews": p.get("reviewsCount"), "maps_address": p.get("address"),
            "maps_phone": p.get("phoneNumber"), "maps_website": p.get("website"),
            "maps_category": p.get("category"), "maps_lat": p.get("latitude"),
            "maps_lng": p.get("longitude"),
        }

    places_leads_raw = []
    for p in places:
        places_leads_raw.append({
            "title": p.get("title", ""), "link": p.get("website", "") or "",
            "snippet": p.get("address", "") or "", "position": None, "is_place": True,
        })

    # Organic searches
    all_raw_results = []
    for qi, query in enumerate(query_variations):
        print(f"[{search_id}] Busca {queries_done + 1}/{queries_total}: {query}")
        search_data = serper_search(query)
        queries_done += 1
        summary_progress["queries_done"] = queries_done
        summary_progress["current_query"] = f"Busca {queries_done}/{queries_total}: {query}"

        organic = search_data.get("organic", [])
        ads = search_data.get("ads", [])
        for result in organic:
            result["_query_index"] = qi
            result["_query"] = query
            all_raw_results.append(result)
        for ad in ads:
            ad["_is_ad"] = True
            ad["_query_index"] = qi
            all_raw_results.append(ad)

        save_search(search_id, {
            "status": "discovering",
            "summary": {
                "search_id": search_id, "niche": niche, "city": city, "state": state,
                "query": base_query, "query_variations": query_variations,
                **summary_progress,
            },
            "leads": [],
        })

        if qi < len(query_variations) - 1:
            time.sleep(2)

    for p in places_leads_raw:
        all_raw_results.append(p)

    print(f"[{search_id}] Raw results collected: {len(all_raw_results)}")

    # Filter spam
    filtered_results = [r for r in all_raw_results if not is_spam_result(r)]
    print(f"[{search_id}] After spam filter: {len(filtered_results)} (removed {len(all_raw_results) - len(filtered_results)})")

    # Process into lead objects
    leads = []
    lead_idx = 0
    for result in filtered_results:
        lead = {
            "id": f"{search_id}_{lead_idx}", "search_id": search_id,
            "title": result.get("title", ""), "link": result.get("link", ""),
            "snippet": result.get("snippet", ""), "position": result.get("position"),
            "tem_site": False, "tem_instagram": False, "tem_facebook": False,
            "tem_ads": False, "tem_maps": False, "instagram_url": "", "facebook_url": "",
            "site_url": "", "ads_type": "", "site_emails": [], "site_phones": [],
            "site_instagram": "", "site_facebook": "", "site_youtube": "", "site_tiktok": "",
            "cnpj_source": "", "enrichment_status": "pending",
        }

        link_lower = result.get("link", "").lower()
        snippet_lower = result.get("snippet", "").lower()
        title_lower = result.get("title", "").lower()
        combined = f"{link_lower} {snippet_lower} {title_lower}"

        if "instagram.com" in link_lower:
            lead["tem_instagram"] = True
            lead["instagram_url"] = result.get("link", "")
        elif "instagram.com" in snippet_lower:
            lead["tem_instagram"] = True
            m = re.search(r"instagram\.com/[\w.]+", combined)
            if m: lead["instagram_url"] = f"https://{m.group()}"

        if "facebook.com" in link_lower:
            lead["tem_facebook"] = True
            lead["facebook_url"] = result.get("link", "")
        elif "facebook.com" in combined:
            lead["tem_facebook"] = True

        is_social = any(d in link_lower for d in [
            "instagram.com", "facebook.com", "youtube.com", "tiktok.com",
            "linkedin.com", "google.com", "maps.google",
        ])
        if link_lower and not is_social and link_lower.startswith("http"):
            lead["tem_site"] = True
            lead["site_url"] = result.get("link", "")

        # Match with Places (fuzzy)
        for pname, pdata in places_map.items():
            lead_words = set(title_lower.split())
            place_words = set(pname.split())
            if lead_words and place_words:
                overlap = len(lead_words & place_words) / max(len(lead_words), len(place_words), 1)
                if overlap >= 0.4:
                    lead.update(pdata)
                    lead["tem_maps"] = True
                    if pdata.get("maps_website"):
                        lead["tem_site"] = True
                        if not lead["site_url"]:
                            lead["site_url"] = pdata["maps_website"]
                    break

        cnpj = extract_cnpj(combined)
        if cnpj and len(cnpj) >= 14:
            lead["cnpj_hint"] = cnpj
            lead["cnpj_source"] = "snippet"

        if result.get("_is_ad"):
            lead["tem_ads"] = True
            lead["ads_type"] = "Google Ads"

        leads.append(lead)
        lead_idx += 1

    # Dedup
    deduped = _deduplicate_leads(leads)
    print(f"[{search_id}] After dedup: {len(deduped)} (removed {len(leads) - len(deduped)})")
    deduped = deduped[:max_results]

    # Mark ads
    ads_results = [r for r in all_raw_results if r.get("_is_ad")]
    for ad in ads_results:
        ad_title = ad.get("title", "").lower()
        for lead in deduped:
            lead_title = lead.get("title", "").lower()
            if ad_title and (ad_title in lead_title or lead_title in ad_title):
                lead["tem_ads"] = True
                lead["ads_type"] = "Google Ads"

    # Re-index
    for i, lead in enumerate(deduped):
        lead["id"] = f"{search_id}_{i}"

    total = len(deduped)
    com_site = sum(1 for l in deduped if l.get("tem_site"))
    com_insta = sum(1 for l in deduped if l.get("tem_instagram"))
    com_maps = sum(1 for l in deduped if l.get("tem_maps"))
    com_ads = sum(1 for l in deduped if l.get("tem_ads"))

    summary = {
        "search_id": search_id, "niche": niche, "city": city, "state": state,
        "query": base_query, "query_variations": query_variations,
        "queries_total": queries_total, "queries_done": queries_done,
        "raw_results": len(all_raw_results), "after_filter": len(filtered_results),
        "total_results": total, "com_site": com_site, "com_instagram": com_insta,
        "com_maps": com_maps, "com_ads": com_ads, "com_cnpj": 0,
        "sem_site": total - com_site, "sem_instagram": total - com_insta,
        "sem_maps": total - com_maps,
        "pct_sem_site": round((total - com_site) / total * 100) if total else 0,
        "pct_sem_instagram": round((total - com_insta) / total * 100) if total else 0,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    data = {"status": "discovery", "summary": summary, "leads": deduped}
    set_status(data, "discovery")
    save_search(search_id, data)
    print(f"[{search_id}] Discovery done: {total} leads (from {len(all_raw_results)} raw)")
    return data


# Need re import
import re


# ─── STEP 1b: CNAE Discovery (via local RF API with Serper fallback) ───

def _cnae_discovery_via_rf_api(cnae: str, city: str, state: str, limit: int, active_only: bool, search_id: str) -> list[dict] | None:
    """
    Query the local CNPJ API (Node.js service backed by RF SQLite database).
    Returns list of lead dicts or None if the API is unavailable.
    """
    from app.config.settings import CNPJ_API_URL
    import urllib.parse

    city_upper = city.upper().strip()
    url = (
        f"{CNPJ_API_URL}/empresas/buscar"
        f"?cnae={urllib.parse.quote(cnae)}"
        f"&cidade={urllib.parse.quote(city_upper)}"
        f"&limit={min(limit, 200)}"
        f"&offset=0"
        f"&active_only={'true' if active_only else 'false'}"
    )

    try:
        import requests
        resp = requests.get(url, timeout=15)
        if not resp.ok:
            print(f"[{search_id}] CNPJ API error: {resp.status_code}")
            return None
        data = resp.json()
        empresas = data.get("empresas", [])
        if not empresas:
            return []

        leads = []
        for i, emp in enumerate(empresas):
            end = emp.get("endereco", {})
            contato = emp.get("contato", {})
            cnae_info = emp.get("cnae", {})

            nf = (emp.get("nomeFantasia") or "").strip()
            rz = (emp.get("razaoSocial") or "").strip()
            cnpj_fmt = emp.get("cnpjFormatado") or emp.get("cnpj", "")
            title_fallback = nf or rz or f"Empresa CNPJ {cnpj_fmt}"
            # Build a lead compatible with the existing pipeline model
            lead = {
                "id": f"{search_id}_{i}",
                "search_id": search_id,
                "title": title_fallback,
                "razao_social": emp.get("razaoSocial", ""),
                "link": "",
                "snippet": f"{end.get('logradouro', '')} {end.get('numero', '')}, {end.get('bairro', '')}".strip(),
                "position": i + 1,
                "tem_site": False,
                "tem_instagram": False,
                "tem_facebook": False,
                "tem_ads": False,
                "tem_maps": False,
                "instagram_url": "",
                "facebook_url": "",
                "site_url": "",
                "site_emails": [],
                "site_phones": [],
                "site_instagram": "",
                "site_facebook": "",
                "site_youtube": "",
                "site_tiktok": "",
                # Pre-fill CNPJ data from RF — skip BrasilAPI lookup in enrich
                "cnpj": emp.get("cnpj", "").replace(".", "").replace("/", "").replace("-", ""),
                "cnpj_source": "receita_federal",
                "situacao": emp.get("situacaoCadastral", ""),
                "data_inicio": emp.get("dataInicioAtividade", ""),
                "cnae_descricao": cnae_info.get("descricao", ""),
                "porte": emp.get("porte", ""),
                "maps_address": f"{end.get('logradouro', '')} {end.get('numero', '')}, {end.get('bairro', '')}, {end.get('municipio', '')} - {end.get('uf', '')}".strip(", "),
                "maps_phone": contato.get("telefone1", ""),
                "email_receita": contato.get("email", ""),
                "enrichment_status": "partial",  # already has CNPJ data
                "cnae_busca": cnae,
            }

            # Set site if email or phone available
            if contato.get("telefone1"):
                lead["site_phones"] = [contato["telefone1"]]
            if contato.get("email"):
                lead["site_emails"] = [contato["email"]]

            leads.append(lead)

        print(f"[{search_id}] CNPJ API returned {len(leads)} companies for CNAE={cnae}, city={city}")
        return leads

    except Exception as e:
        print(f"[{search_id}] CNPJ API unavailable: {e}")
        return None


def run_cnae_discovery(cnae: str, city: str, state: str, limit: int = 50, active_only: bool = True, search_id: str = None) -> dict:
    """
    Discovery via CNAE code.
    Uses local RF API (CNPJ database) when available, falls back to Serper.
    """
    if search_id is None:
        search_id = str(uuid.uuid4())[:8]

    cnae_clean = cnae.replace("-", "").replace("/", "").replace(".", "")
    cnae_label = f"CNAE {cnae_clean}"

    print(f"[{search_id}] CNAE search: {cnae_clean} in {city}/{state}, limit={limit}")

    # Save initial state
    save_search(search_id, {
        "status": "discovering",
        "summary": {
            "search_id": search_id,
            "niche": cnae_label,
            "city": city,
            "state": state,
            "cnae": cnae_clean,
            "search_type": "cnae",
            "query": f"CNAE {cnae_clean} {city} {state}",
            "queries_total": 1,
            "queries_done": 0,
            "current_query": "Consultando base da Receita Federal...",
            "total_results": 0,
            "com_site": 0, "sem_site": 0, "pct_sem_site": 0,
            "com_instagram": 0, "com_ads": 0, "com_maps": 0, "com_cnpj": 0,
        },
        "leads": [],
    })

    # ── Try local RF API first ──
    rf_leads = _cnae_discovery_via_rf_api(cnae_clean, city, state, limit, active_only, search_id)

    if rf_leads is not None:
        # RF API available — use its results directly
        leads = rf_leads[:limit]
        source = "receita_federal"
        print(f"[{search_id}] Using RF API: {len(leads)} leads")
    else:
        # RF API unavailable — fall back to Serper
        print(f"[{search_id}] RF API unavailable, falling back to Serper")
        source = "serper"

        # CNAE label lookup for better Serper queries
        CNAE_LABELS = {
            "9521": "reparação eletrodomésticos", "9522": "reparação equipamentos domésticos",
            "9511": "reparação computadores", "9512": "reparação equipamentos comunicação",
            "4771": "farmácia drogaria", "4772": "perfumaria cosméticos",
            "5611": "restaurante", "5612": "lanchonete", "5630": "bar",
            "8621": "serviços médicos", "8622": "serviços odontológicos",
            "9313": "academia ginástica", "6201": "desenvolvimento software",
            "4744": "material elétrico", "4741": "materiais construção",
        }
        cnae_prefix = cnae_clean[:4]
        cnae_label = CNAE_LABELS.get(cnae_prefix, f"CNAE {cnae_clean}")

        queries = [
            f"{cnae_label} {city} {state}",
            f"{cnae_label} {city}",
            f"{cnae_label} empresa {city}",
        ][:MAX_QUERY_VARIATIONS]

        # Places search
        places_data = serper_search(queries[0], "places")
        places = places_data.get("places", [])
        places_map = {}
        for p in places:
            name = p.get("title", "").lower()
            places_map[name] = {
                "maps_title": p.get("title"), "maps_rating": p.get("rating"),
                "maps_reviews": p.get("reviewsCount"), "maps_address": p.get("address"),
                "maps_phone": p.get("phoneNumber"), "maps_website": p.get("website"),
                "maps_category": p.get("category"),
            }

        all_raw = []
        for qi, query in enumerate(queries):
            search_data = serper_search(query, num=100)
            for result in search_data.get("organic", []):
                all_raw.append(result)
            if qi < len(queries) - 1:
                time.sleep(2)

        filtered = [r for r in all_raw if not is_spam_result(r)]
        leads = []
        for idx, result in enumerate(filtered):
            lead = {
                "id": f"{search_id}_{idx}", "search_id": search_id,
                "title": result.get("title", ""), "link": result.get("link", ""),
                "snippet": result.get("snippet", ""), "position": result.get("position"),
                "tem_site": False, "tem_instagram": False, "tem_facebook": False,
                "tem_ads": False, "tem_maps": False,
                "instagram_url": "", "facebook_url": "", "site_url": "",
                "site_emails": [], "site_phones": [],
                "site_instagram": "", "site_facebook": "", "site_youtube": "", "site_tiktok": "",
                "cnpj_source": "", "enrichment_status": "pending",
                "cnae_busca": cnae_clean,
            }
            link_lower = result.get("link", "").lower()
            if not any(d in link_lower for d in ["instagram.com", "facebook.com", "youtube.com", "tiktok.com", "google.com"]):
                if link_lower.startswith("http"):
                    lead["tem_site"] = True
                    lead["site_url"] = result.get("link", "")
            for pname, pdata in places_map.items():
                title_lower = lead["title"].lower()
                if set(title_lower.split()) & set(pname.split()):
                    lead.update(pdata)
                    lead["tem_maps"] = True
                    if pdata.get("maps_website") and not lead["site_url"]:
                        lead["tem_site"] = True
                        lead["site_url"] = pdata["maps_website"]
                    break
            leads.append(lead)

        leads = _deduplicate_leads(leads)[:limit]

    # Re-index
    for i, lead in enumerate(leads):
        lead["id"] = f"{search_id}_{i}"

    total = len(leads)
    com_site = sum(1 for l in leads if l.get("tem_site"))
    com_insta = sum(1 for l in leads if l.get("tem_instagram"))
    com_maps = sum(1 for l in leads if l.get("tem_maps"))
    com_cnpj = sum(1 for l in leads if l.get("cnpj"))

    summary = {
        "search_id": search_id,
        "niche": cnae_label,
        "city": city,
        "state": state,
        "cnae": cnae_clean,
        "search_type": "cnae",
        "source": source,
        "query": f"CNAE {cnae_clean} {city} {state}",
        "queries_total": 1,
        "queries_done": 1,
        "total_results": total,
        "com_site": com_site, "com_instagram": com_insta,
        "com_maps": com_maps, "com_ads": 0, "com_cnpj": com_cnpj,
        "sem_site": total - com_site, "sem_instagram": total - com_insta,
        "sem_maps": total - com_maps,
        "pct_sem_site": round((total - com_site) / total * 100) if total else 0,
        "pct_sem_instagram": round((total - com_insta) / total * 100) if total else 0,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    result_data = {"status": "discovery", "summary": summary, "leads": leads}
    set_status(result_data, "discovery")
    save_search(search_id, result_data)
    print(f"[{search_id}] CNAE discovery done: {total} leads (source: {source})")
    return result_data


# ─── STEP 2: Enrich (CNPJ + Site scraping) ───

def run_enrich(search_id: str) -> dict | None:
    data = load_search(search_id)
    if not data:
        return None
    set_status(data, "enriching")
    save_search(search_id, data)

    leads = data["leads"]
    enriched_count = 0

    for i, lead in enumerate(leads):
        if lead.get("cnpj"):
            continue

        cnpj = lead.get("cnpj_hint")
        if not cnpj:
            combined = f"{lead.get('link', '')} {lead.get('snippet', '')} {lead.get('title', '')}"
            cnpj = extract_cnpj(combined)

        if cnpj and len(cnpj) >= 14:
            cnpj_data = check_cnpj(cnpj)
            if cnpj_data:
                lead["cnpj"] = cnpj
                lead["razao_social"] = cnpj_data.get("razao_social", "")
                lead["situacao"] = str(cnpj_data.get("descricao_situacao_cadastral", ""))
                lead["capital_social"] = cnpj_data.get("capital_social", 0)
                lead["data_inicio"] = cnpj_data.get("data_inicio_atividade", "")
                lead["opcao_pelo_mei"] = cnpj_data.get("opcao_pelo_mei", False)
                lead["opcao_pelo_simples"] = cnpj_data.get("opcao_pelo_simples", False)
                lead["cnae_descricao"] = cnpj_data.get("cnae_fiscal_descricao", "")
                lead["natureza_juridica"] = cnpj_data.get("natureza_juridica", "")
                lead["porte"] = cnpj_data.get("porte", "")
                lead["email_receita"] = cnpj_data.get("email", "")
                lead["telefone_receita"] = f"{cnpj_data.get('ddd_telefone_1', '')}"
                qsa = cnpj_data.get("qsa", [])
                if qsa:
                    lead["socios"] = [s.get("nome_socio", s.get("nome", "")) for s in qsa]
                enriched_count += 1
                print(f"[{search_id}] Enriched {i+1}/{len(leads)}: {lead.get('title', '?')} → CNPJ {cnpj}")

            if (i + 1) % 5 == 0:
                time.sleep(0.3)

    # Site scraping
    print(f"[{search_id}] Starting site scraping for {len(leads)} leads...")
    for i, lead in enumerate(leads):
        if lead.get("enrichment_status") == "done":
            continue

        urls_to_fetch = []
        if lead.get("site_url"):
            urls_to_fetch.append(("site", lead["site_url"]))
        if lead.get("maps_website") and lead.get("maps_website") != lead.get("site_url"):
            urls_to_fetch.append(("maps", lead["maps_website"]))

        # If no site is known, use Serper to find it
        if not urls_to_fetch and lead.get("title") and not lead.get("title").startswith("Empresa CNPJ"):
            try:
                city = data.get("summary", {}).get("city", "")
                query = f"{lead.get('title')} {city} site oficial"
                search_data = serper_search(query, num=3)
                for res in search_data.get("organic", []):
                    link = res.get("link", "").lower()
                    if not any(d in link for d in ["instagram.com", "facebook.com", "youtube.com", "tiktok.com", "google.com", "jusbrasil.com.br", "reclameaqui.com.br"]):
                        lead["site_url"] = res.get("link")
                        lead["tem_site"] = True
                        urls_to_fetch.append(("serper", lead["site_url"]))
                        break
            except Exception as e:
                print(f"  [{search_id}] Error searching site via Serper: {e}")

        any_data_found = False
        for source, url in urls_to_fetch:
            try:
                print(f"  [{search_id}] Fetching {source}: {url} ({i+1}/{len(leads)})")
                site_data = fetch_site_data(url)
                if site_data["emails"] and not lead.get("site_emails"):
                    lead["site_emails"] = site_data["emails"]; any_data_found = True
                if site_data["phones"] and not lead.get("site_phones"):
                    lead["site_phones"] = site_data["phones"]; any_data_found = True
                if site_data["instagram"] and not lead.get("site_instagram"):
                    lead["site_instagram"] = site_data["instagram"]; any_data_found = True
                if site_data["facebook"] and not lead.get("site_facebook"):
                    lead["site_facebook"] = site_data["facebook"]; any_data_found = True
                if site_data["youtube"] and not lead.get("site_youtube"):
                    lead["site_youtube"] = site_data["youtube"]; any_data_found = True
                if site_data["tiktok"] and not lead.get("site_tiktok"):
                    lead["site_tiktok"] = site_data["tiktok"]; any_data_found = True
                if site_data["cnpj"] and not lead.get("cnpj"):
                    cnpj_data = check_cnpj(site_data["cnpj"])
                    if cnpj_data:
                        lead["cnpj"] = site_data["cnpj"]
                        lead["cnpj_source"] = f"site ({source})"
                        lead["razao_social"] = cnpj_data.get("razao_social", "")
                        lead["situacao"] = str(cnpj_data.get("descricao_situacao_cadastral", ""))
                        lead["capital_social"] = cnpj_data.get("capital_social", 0)
                        lead["data_inicio"] = cnpj_data.get("data_inicio_atividade", "")
                        lead["opcao_pelo_mei"] = cnpj_data.get("opcao_pelo_mei", False)
                        lead["opcao_pelo_simples"] = cnpj_data.get("opcao_pelo_simples", False)
                        lead["cnae_descricao"] = cnpj_data.get("cnae_fiscal_descricao", "")
                        lead["natureza_juridica"] = cnpj_data.get("natureza_juridica", "")
                        lead["porte"] = cnpj_data.get("porte", "")
                        lead["email_receita"] = cnpj_data.get("email", "")
                        lead["telefone_receita"] = f"{cnpj_data.get('ddd_telefone_1', '')}"
                        qsa = cnpj_data.get("qsa", [])
                        if qsa:
                            lead["socios"] = [s.get("nome_socio", s.get("nome", "")) for s in qsa]
                        enriched_count += 1
                time.sleep(1)
            except Exception as e:
                print(f"  [{search_id}] Error fetching {url}: {e}")
                continue

        # CNPJ from phone
        if not lead.get("cnpj") and lead.get("maps_phone"):
            try:
                cnpj_from_phone = try_cnpj_from_phone(lead["maps_phone"])
                if cnpj_from_phone:
                    cnpj_data = check_cnpj(cnpj_from_phone)
                    if cnpj_data:
                        lead["cnpj"] = cnpj_from_phone
                        lead["cnpj_source"] = "maps_phone"
                        lead["razao_social"] = cnpj_data.get("razao_social", "")
                        lead["situacao"] = str(cnpj_data.get("descricao_situacao_cadastral", ""))
                        lead["capital_social"] = cnpj_data.get("capital_social", 0)
                        lead["data_inicio"] = cnpj_data.get("data_inicio_atividade", "")
                        lead["opcao_pelo_mei"] = cnpj_data.get("opcao_pelo_mei", False)
                        lead["opcao_pelo_simples"] = cnpj_data.get("opcao_pelo_simples", False)
                        lead["cnae_descricao"] = cnpj_data.get("cnae_fiscal_descricao", "")
                        lead["natureza_juridica"] = cnpj_data.get("natureza_juridica", "")
                        lead["porte"] = cnpj_data.get("porte", "")
                        lead["email_receita"] = cnpj_data.get("email", "")
                        lead["telefone_receita"] = f"{cnpj_data.get('ddd_telefone_1', '')}"
                        qsa = cnpj_data.get("qsa", [])
                        if qsa:
                            lead["socios"] = [s.get("nome_socio", s.get("nome", "")) for s in qsa]
                        enriched_count += 1
                    time.sleep(0.5)
            except Exception as e:
                print(f"  [{search_id}] Error CNPJ from phone: {e}")

        if lead.get("cnpj"):
            lead["enrichment_status"] = "done"
        elif any_data_found:
            lead["enrichment_status"] = "partial"
        else:
            lead["enrichment_status"] = "done"

        if (i + 1) % 5 == 0:
            save_search(search_id, data)

    # Update summary
    com_cnpj = sum(1 for l in leads if l.get("cnpj"))
    com_site_email = sum(1 for l in leads if l.get("site_emails"))
    com_site_phone = sum(1 for l in leads if l.get("site_phones"))
    com_youtube = sum(1 for l in leads if l.get("site_youtube"))
    com_tiktok = sum(1 for l in leads if l.get("site_tiktok"))
    data["summary"]["com_cnpj"] = com_cnpj
    data["summary"]["com_site_email"] = com_site_email
    data["summary"]["com_site_phone"] = com_site_phone
    data["summary"]["com_youtube"] = com_youtube
    data["summary"]["com_tiktok"] = com_tiktok

    set_status(data, "enriched")
    save_search(search_id, data)
    print(f"[{search_id}] Enrichment done: {enriched_count} CNPJs enriched, {com_cnpj} total with CNPJ")
    return data


# ─── STEP 3: Score ───

def run_score(search_id: str) -> dict | None:
    data = load_search(search_id)
    if not data:
        return None
    leads = data["leads"]
    for lead in leads:
        lead["score"] = calculate_score(lead)
    leads.sort(key=lambda x: x.get("score", 0), reverse=True)
    data["leads"] = leads
    set_status(data, "scored")
    save_search(search_id, data)
    print(f"[{search_id}] Scoring done")
    return data


# ─── STEP 4: Market Analysis (IA) ───

def run_analyze_market(search_id: str) -> dict | None:
    data = load_search(search_id)
    if not data:
        return None

    # Migrate legacy field
    if data["summary"].get("ia_analysis") and not data["summary"].get("ia_market_analysis"):
        data["summary"]["ia_market_analysis"] = data["summary"].pop("ia_analysis")
        save_search(search_id, data)

    leads = data["leads"]
    s = data["summary"]
    total = s.get("total_results", len(leads))
    com_site = s.get("com_site", 0)
    com_insta = s.get("com_instagram", 0)
    com_maps = s.get("com_maps", 0)
    com_ads = s.get("com_ads", 0)
    com_cnpj = s.get("com_cnpj", 0)
    com_site_email = s.get("com_site_email", 0)
    com_site_phone = s.get("com_site_phone", 0)
    pct_sem_site = s.get("pct_sem_site", round((total - com_site) / total * 100) if total else 0)
    pct_sem_instagram = s.get("pct_sem_instagram", round((total - com_insta) / total * 100) if total else 0)

    ia_prompt = f"""Gere um relatório EXECUTIVO e CONCISO de mercado. Seja direto e prático, sem enrolação.

DADOS: {total} empresas em {s['niche']} em {s['city']}/{s['state']}.
- % sem site: {pct_sem_site}%
- % sem Instagram: {pct_sem_instagram}%
- % sem Google Ads: 100%
- Com Google Maps: {com_maps}

RESPONDA EM FORMATO JSON:
{{
  "resumo": "2-3 frases resumindo o mercado",
  "pontos_fracos": ["fraco 1", "fraco 2", "fraco 3"],
  "oportunidades": [
    {{"titulo": "nome", "descricao": "1 frase", "potencial": "alto/medio/baixo"}}
  ],
  "estrategia_entrada": "1 parágrafo direto sobre como abordar este nicho",
  "ticket_medio_estimado": "R$ X.XXX",
  "concorrencia": "baixa/media/alta - 1 frase"
}}

Responda APENAS com o JSON, sem markdown, sem ```json, sem texto antes ou depois."""

    ia_result = ai_analyze(ia_prompt, timeout=120, max_tokens=3000)
    parsed = parse_json_from_ia(ia_result)
    if parsed and isinstance(parsed, dict):
        data["summary"]["ia_market_analysis"] = parsed
    else:
        data["summary"]["ia_market_analysis"] = ia_result
    data["summary"].pop("ia_analysis", None)

    set_status(data, "market_analyzed")
    save_search(search_id, data)
    print(f"[{search_id}] Market analysis done")
    return data


# ─── STEP 5: Lead Analysis (IA) — incremental ───

def run_analyze_leads(search_id: str, count: int = 1) -> dict | None:
    data = load_search(search_id)
    if not data:
        return None

    leads = data["leads"]
    s = data["summary"]
    total_to_analyze = _get_total_to_analyze(leads)

    if "analyzed_count" not in s:
        s["analyzed_count"] = sum(1 for l in leads if l.get("ia_analise"))
    if "total_to_analyze" not in s:
        s["total_to_analyze"] = total_to_analyze

    candidates = leads[:total_to_analyze]
    to_analyze = [l for l in candidates if not l.get("ia_analise")]

    analyzed_this_call = 0
    total = s.get("total_results", len(leads))
    com_site = s.get("com_site", 0)
    com_insta = s.get("com_instagram", 0)

    for lead in to_analyze[:count]:
        lead_site_url = lead.get("site_url") or lead.get("maps_website") or "Não possui"
        lead_instagram = lead.get("instagram_url") or lead.get("site_instagram") or "Não possui"
        lead_facebook = lead.get("facebook_url") or lead.get("site_facebook") or "Não possui"
        lead_maps = f"{lead.get('maps_rating', '-')} estrelas ({lead.get('maps_reviews', '-')} avaliações)" if lead.get("tem_maps") else "Não possui"
        lead_phone = lead.get("maps_phone") or (", ".join(lead.get("site_phones", [])) if lead.get("site_phones") else None) or "Não encontrado"
        lead_cnpj = lead.get("cnpj") or "Não encontrado"

        lead_prompt = f"""Descreva esta empresa de forma CLARA e INFORMATIVA — visão geral, não proposta de vendas.

EMPRESA: {lead.get('title')}
- Site: {lead_site_url}
- Instagram: {lead_instagram}
- Facebook: {lead_facebook}
- Google Maps: {lead_maps}
- Telefone: {lead_phone}
- CNPJ: {lead_cnpj}
- Score presença digital: {lead.get('score', 0)}/100

CONTEXTO DO MERCADO: {s.get('niche', '')} em {s.get('city', '')}/{s.get('state', '')}, {total} empresas, {s.get('pct_sem_site', 0)}% sem site, {s.get('pct_sem_instagram', 0)}% sem Instagram

RESPONDA EM FORMATO JSON:
{{
  "resumo": "2-3 frases descrevendo quem é a empresa e sua situação digital",
  "presenca_digital": "Descrição da presença online (site, redes sociais, Google)",
  "posicao_mercado": "Como se compara aos concorrentes locais",
  "publico_esperado": "Perfil estimado de clientes dessa empresa",
  "observacoes": "Algo relevante notável sobre a empresa"
}}

Responda APENAS com o JSON, sem markdown, sem ```json, sem texto antes ou depois."""

        try:
            ia_lead = ai_analyze(lead_prompt, timeout=90, max_tokens=2000)
            if "temporariamente indisponível" in ia_lead:
                lead["ia_analise"] = "⏳ IA indisponível para este lead. Tente novamente."
            else:
                parsed = parse_json_from_ia(ia_lead)
                if parsed and isinstance(parsed, dict):
                    lead["ia_analise"] = parsed
                else:
                    lead["ia_analise"] = ia_lead
        except Exception as e:
            print(f"  [{search_id}] IA error for lead {lead.get('title')}: {e}")
            lead["ia_analise"] = "⏳ IA indisponível para este lead. Tente novamente."

        analyzed_this_call += 1
        idx = leads.index(lead) + 1
        print(f"[{search_id}] Lead analysis {idx}/{total_to_analyze} done: {lead.get('title', '?')}")

    s["analyzed_count"] = sum(1 for l in leads[:total_to_analyze] if l.get("ia_analise"))
    s["total_to_analyze"] = total_to_analyze

    remaining = [l for l in leads[:total_to_analyze] if not l.get("ia_analise")]
    if not remaining:
        set_status(data, "analyzed")
    else:
        set_status(data, "analyzing_leads")

    save_search(search_id, data)
    print(f"[{search_id}] Lead analysis: +{analyzed_this_call} this call, {s['analyzed_count']}/{s['total_to_analyze']} total")
    return data
