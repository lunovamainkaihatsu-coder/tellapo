# -*- coding: utf-8 -*-
# =========================================================
# 📞 AIアポ練習くん v1.6（台本オート生成版｜BGM/SEなし）
# - 会話ロープレ（既存機能は軽量化して存続）
# - スコア/XP
# - 📝 台本オート生成（履歴→台本 / 雛形→台本）
# - Markdown/JSON で保存＆ダウンロード（data/ai_apo_practice/scripts/ にも自動保存）
# - APIキー未設定でもローカル雛形で生成可能（フォールバック）
# =========================================================

import os
import json
import time
import re
import datetime
from pathlib import Path

from PIL import Image
import streamlit as st
from openai import OpenAI

# ---------- 基本 ----------
APP_TITLE = "AIアポ練習くん v1.6"
CONGRATS_THRESHOLD = 7  # しきい値は今後UI化予定

# ---------- OpenAI APIキー ----------
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

if not api_key:
    st.error("OPENAI_API_KEY が設定されていません。Streamlit Cloud の Secrets に設定してください。")
    st.stop()

client = OpenAI(api_key=api_key)

# ---------- パス設定 ----------
ROOT = Path(__file__).resolve().parents[2]  # .../Luna-app
APP_ID = "ai_apo_practice"

DATA_DIR = ROOT / "data" / APP_ID
DATA_DIR.mkdir(parents=True, exist_ok=True)

SCRIPTS_DIR = DATA_DIR / "scripts"
SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

ASSETS = ROOT / "assets"
INSTR_ASSETS = ASSETS / "instructors"

INST2DIR = {
    "優しいお姉さん": "oneesan",
    "厳しめ上司": "boss",
    "営業の仙人": "sennin",
}

EMO_TO_FILE = {
    "喜": "joy",
    "通常": "neutral",
    "困": "trouble",
    "怒": "anger",
    "驚": "surprise",
}# ---------- ペルソナ/採点 ----------
PERSONAS = {
    "穏やかな担当者（入門）":{
        "desc":"基本的に丁寧。こちらの話を聞くが、目的が曖昧だと流れる。",
        "system":"あなたは会社の総務担当。穏やかで礼儀正しい。丁寧に受け答えするが、用件がはっきりしないなら断る。"
    },
    "多忙な社長（中級）":{
        "desc":"時間がない。要点が出ないと即切り。価値が伝われば面談OK。",
        "system":"あなたは中小企業の社長。時間が最重要。結論・価値・具体性がない話は即断る。価値が明確なら短時間面談を認める。"
    },
    "冷たい担当（上級）":{
        "desc":"興味薄。反論多め。根拠と実績で突破が必要。",
        "system":"あなたはITの情シス担当。売り込みに慣れており、興味が薄い。根拠・実績・導入効果が弱いと冷たく断る。"
    }
}
CHECKS = {
    "挨拶・名乗り":[r"(お世話になっております|はじめまして|失礼いたします)", r"(申|もう)し上げます|と申します"],
    "目的の明確化":[r"(本日|本日は).*(ご連絡|お電話|お時間).*理由", r"(ご提案|ご案内|打ち合わせ|面談)"],
    "相手配慮":[r"(お時間.*大丈夫|今お忙しい|少しだけ|手短に)"],
    "価値訴求":[r"(コスト|工数|売上|効率|効果|改善|事例|実績)"],
    "質問・確認":[r"(ご興味|もしよろしけれ|いかが|ご都合|ご判断|ご検討)"],
    "クロージング":[r"(来週|今週|[0-9０-９]日|[0-9０-９]時|日程|面談|打ち合わせ).*よろしい"]
}
WEIGHTS = {"挨拶・名乗り":1,"目的の明確化":2,"相手配慮":1,"価値訴求":3,"質問・確認":1,"クロージング":2}
MAX_SCORE = sum(WEIGHTS.values())

def score_turn(all_user_text: str):
    sc=0; detail={}
    for k,pats in CHECKS.items():
        ok = any(re.search(p, all_user_text) for p in pats)
        detail[k]=ok
        if ok: sc += WEIGHTS[k]
    return sc, detail

# ---------- 感情ロジック（簡易） ----------
def emotion_from_progress(score, turns):
    ratio = (score/MAX_SCORE) if MAX_SCORE else 0
    if ratio>=.8: return "喜"
    if ratio>=.5: return "通常"
    if ratio>=.3: return "困"
    return "通常" if turns<2 else "怒"

@st.cache_resource
def preload_emotions(inst):
    d = INST2DIR.get(inst); out={}
    if not d: return out
    for emo,slug in EMO_TO_FILE.items():
        p = INSTR_ASSETS / d / f"{slug}.png"
        if p.exists():
            try: out[emo]=Image.open(p).copy()
            except: pass
    if "通常" not in out:
        base = INSTR_ASSETS / d / "neutral.png"
        if base.exists(): out["通常"]=Image.open(base).copy()
    return out

def load_avatar(inst, emo):
    d = INST2DIR.get(inst); slug = EMO_TO_FILE.get(emo,"neutral")
    if not d: return None
    p = INSTR_ASSETS / d / f"{slug}.png"
    if p.exists():
        try: return Image.open(p)
        except: pass
    base = INSTR_ASSETS / d / "neutral.png"
    if base.exists():
        try: return Image.open(base)
        except: pass
    fb = INSTR_ASSETS / f"{d}.png"
    return Image.open(fb) if fb.exists() else None

# ---------- OpenAI 応答 ----------
def ai_reply(persona_key, history, role, industry, product, calltime, max_tokens=180):
    # API未設定のときは固定応答（軽量）
    if not os.getenv("OPENAI_API_KEY"):
        t = history[-1]["content"] if history else ""
        if re.search(r"(忙|時間がない)", t): return "いま立て込んでまして…要点と所要時間を一言でお願いします。", 0.0
        if re.search(r"(事例|実績|効果)", t): return "その実績、当社に近い業界の話はありますか？", 0.0
        if re.search(r"(面談|日程|来週|今週|15分)", t): return "15分でしたら火曜10時か木曜15時なら検討できます。", 0.0
        return "恐れ入ります、要点と価値を一言でお願いできますか？", 0.0
    sys = PERSONAS[persona_key]["system"]
    guide = ("あなたは電話の相手（顧客）です。最後は必ず1〜2文で返答。\n"
             "1) 曖昧なら要点・価値・所要時間を求める。2) 断る理由は現実的。3) 価値が明確なら短時間面談を許容。\n"
             f"条件: 立場={role}, 業界={industry}, 商材={product or '未入力'}, 時間帯={calltime}")
    msgs = [{"role":"system","content":sys+"\n"+guide}] + history[-10:]
    t0=time.time()
    res = client.chat.completions.create(model="gpt-4o-mini", messages=msgs, temperature=0.7, max_tokens=max_tokens)
    return res.choices[0].message.content.strip(), time.time()-t0

# ---------- 講評 ----------
INSTRUCTORS = {
  "優しいお姉さん":"優しく共感しつつ、良かった点と改善点を1〜3個だけ。言い過ぎない。",
  "厳しめ上司":"甘さを具体的に指摘。次回の行動を命令口調で3点まで。",
  "営業の仙人":"要点を箇条書きで端的に。最後は短い金言で締める。"
}
MODES = {"ほめほめ":"ポジ中心・改善1点","きびしく":"問題点を率直に・改善アクション明確に"}
def get_advice(history, score, detail, persona, inst, mode):
    if not os.getenv("OPENAI_API_KEY"): return "（今日は講評なしでもOK。回数を重ねよう！）"
    sys = f"""あなたは{inst}です。{INSTRUCTORS[inst]}
モード:{mode}（{MODES[mode]}）
会話の長い要約は不要。日本語で：
- 改善アクション最大3点（箇条書き）
- 必要なら良かった点を1点
- 最後に1行の励まし/叱咤"""
    short = history[-10:]
    msgs = [{"role":"system","content":sys},
            {"role":"user","content":f"相手ペルソナ:{persona}\nスコア:{score}\nチェック:{detail}\n会話履歴:{short}\n"}]
    res = client.chat.completions.create(model="gpt-4o-mini", messages=msgs, temperature=0.6, max_tokens=220)
    return res.choices[0].message.content.strip()

# ---------- XP ----------
PROG = DATA_DIR / "progress.json"
SECRETS = [
  "Lv1: 30秒で『価値・実績・所要時間』の三点提示を覚える。",
  "Lv2: 断り文句（忙しい/予算/既存導入）への切り返しテンプレを準備。",
  "Lv3: 立場別の“知りたい数字”を1つだけ（決裁者:費用対効果/担当:工数/受付:所要時間）。",
  "Lv4: 面談依頼は『具体的な日時』＋『短時間』＋『目的』の三点固定。",
  "Lv5: 反論→確認質問→価値再提示→次アクション提示 をワンセット化。",
]
def load_prog():
    if PROG.exists():
        try: return json.loads(PROG.read_text(encoding="utf-8"))
        except: pass
    return {"xp":0, "level":0}
def save_prog(p): PROG.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
def add_xp(s):
    p = load_prog(); before = p["level"]
    p["xp"] += max(0,int(s))
    while p["xp"] >= (p["level"]+1)*10: p["level"] += 1
    save_prog(p)
    return p, (p["level"]>before)

# ---------- 台本オート生成：土台 ----------
OBJECTIONS = {
    "忙しい": {"切返し":"30秒で要点だけ共有→要点/価値/所要時間を先出し", "一言例":"手短に30秒で結論からお伝えします。"},
    "予算がない": {"切返し":"費用対効果 or 小さく始める案を提示", "一言例":"現状費の見直しで捻出できる見込みが高いです。"},
    "既に導入済": {"切返し":"置換の優位・併用の相性・実績提示", "一言例":"御社環境での切替効果の実測値がありまして…"},
    "興味がない": {"切返し":"相手のKPI/痛点を一つだけ突く", "一言例":"情シスの○○に絞ると、月△時間削減が見込めます。"},
    "決裁フロー": {"切返し":"短時間の一次面談＋次アクション明確化", "一言例":"10分で概要を共有→必要なら決裁者様向け資料を即送付します。"},
}

SCRIPT_TEMPLATE = {
    "タイトル": "テレアポ台本（{industry}向け／商材：{product}）",
    "想定相手": "{persona}（立場：{role}／時間帯：{calltime}）",
    "オープニング": [
        "お世話になっております。◯◯社の△△と申します。30秒ほどお時間よろしいでしょうか？（所要時間提示）",
        "本日は〔何を・誰に・どれくらいの効果があったか〕を、結論から一言で共有します。（価値→実績→所要時間）"
    ],
    "ヒアリング": [
        "現在、【課題候補】のうち、優先度が高いのはどれでしょうか？（A/B/C から選択）",
        "もし差し支えなければ、月あたりの工数や費用感もざっくりで結構なので教えてください。"
    ],
    "価値提案": [
        "御社と近い事例では、{industry}で〔○○を××に〕でき、月{effect_hours}時間の削減でした。",
        "小さく始める場合は、{pilot_scope}の範囲で{pilot_term}運用→効果測定→拡大の流れを想定しています。"
    ],
    "反論処理": [],  # 生成時に OBJECTIONS から埋め込み
    "クロージング": [
        "10〜15分だけ日時をいただければ、{purpose} を絵で共有できます。",
        "（候補）来週{candidates}の{timeslot}のうち、いかがでしょうか？"
    ],
    "締めの一言": "本日はお時間ありがとうございました。要点を資料1枚にまとめて、先ほどの候補から日程をご提案します。"
}

def _mk_default_script(sim_ctx: dict, score: int, level: int) -> dict:
    """APIなしでも使えるローカル雛形生成"""
    cand = "火/木" if sim_ctx.get("calltime") in ("朝","昼") else "水/金"
    timeslot = "10時/15時" if sim_ctx.get("role")!="受付" else "13時/16時"
    t = json.loads(json.dumps(SCRIPT_TEMPLATE))  # deepcopy
    t["タイトル"] = t["タイトル"].format(**sim_ctx)
    t["想定相手"] = t["想定相手"].format(**sim_ctx)
    # 反論処理（3件まで）
    t["反論処理"] = []
    for k,(key,val) in zip(list(OBJECTIONS.keys())[:3], OBJECTIONS.items()):
        tip = f"■「{k}」→ {val['切返し']}｜一言例：{val['一言例']}"
        t["反論処理"].append(tip)
    # クロージングの補完
    t["クロージング"][0] = t["クロージング"][0].format(purpose="御社向け導入イメージ")
    t["クロージング"][1] = t["クロージング"][1].format(candidates=cand, timeslot=timeslot)
    # 価値提案の補完
    t["価値提案"][0] = t["価値提案"][0].format(
        industry=sim_ctx.get("industry",""),
        effect_hours=12 + min(score, 10)
    )
    t["価値提案"][1] = t["価値提案"][1].format(
        pilot_scope="小チーム/1部署",
        pilot_term="2週間〜1ヶ月"
    )
    return t

def generate_script_with_openai(history, sim_ctx: dict, score: int, level: int, tone: str, length: str) -> dict:
    """OpenAIで履歴→台本整形（なければ雛形）"""
    if not os.getenv("OPENAI_API_KEY"):
        return _mk_default_script(sim_ctx, score, level)

    length_hint = {"短め":"要点だけ・各章1〜2行","ふつう":"各章2〜3行","やや長め":"各章3〜5行"}[length]
    sys = f"""あなたは日本語のB2Bテレアポ台本作成アシスタント。
- 構成: オープニング/ヒアリング/価値提案/反論処理/クロージング/締めの一言
- 口調: {tone}、読みやすく、電話想定
- 出力形式: JSON (各章は配列)
- 長さ: {length_hint}
- 反論処理: 「忙しい/予算/導入済み/興味なし/決裁フロー」から3つを簡潔に
- 具体化: 事例・数字・所要時間を入れる
"""
    user = {
        "persona": sim_ctx.get("persona"),
        "role": sim_ctx.get("role"),
        "industry": sim_ctx.get("industry"),
        "product": sim_ctx.get("product"),
        "calltime": sim_ctx.get("calltime"),
        "score": score,
        "level": level,
        "history": history[-12:],
    }
    msgs = [{"role":"system","content":sys},{"role":"user","content":json.dumps(user, ensure_ascii=False)}]
    res = client.chat.completions.create(model="gpt-4o-mini", messages=msgs, temperature=0.5, max_tokens=800)
    txt = res.choices[0].message.content.strip()
    try:
        data = json.loads(txt)
        # タイトル/想定相手のヘッダ補完
        data.setdefault("タイトル", f"テレアポ台本（{sim_ctx.get('industry','')}向け／商材：{sim_ctx.get('product','')}）")
        data.setdefault("想定相手", f"{sim_ctx.get('persona','')}（立場：{sim_ctx.get('role','')}／時間帯：{sim_ctx.get('calltime','')}）")
        return data
    except Exception:
        # JSONで返らなかった時は雛形
        return _mk_default_script(sim_ctx, score, level)

def script_to_markdown(doc: dict) -> str:
    def sec(title, body):
        if isinstance(body, list):
            lines = "\n".join([f"- {x}" for x in body])
            return f"## {title}\n{lines}\n"
        elif isinstance(body, str):
            return f"## {title}\n{body}\n"
        else:
            return f"## {title}\n"
    parts = [f"# {doc.get('タイトル','アポ台本')}\n",
             f"**想定相手**：{doc.get('想定相手','')}（自動生成）\n"]
    for k in ["オープニング","ヒアリング","価値提案","反論処理","クロージング","締めの一言"]:
        if k in doc:
            parts.append(sec(k, doc[k]))
    return "\n".join(parts)

# ---------- State ----------
def init_state():
    ss=st.session_state
    if "chat" not in ss: ss.chat=[{"role":"assistant","content":"はい、◯◯株式会社の（受付）です。ご用件をお願いします。"}]
    defaults = {"ended":False,"score":0,"detail":{},"turns":0,"advice":None,
                "last_rt":None,"persona":"穏やかな担当者（入門）","inst":"優しいお姉さん",
                "role":"受付","industry":"IT","show_congrats":False}
    for k,v in defaults.items():
        if k not in ss: ss[k]=v
init_state()

# ---------- UI ----------
st.set_page_config(page_title=APP_TITLE, page_icon="📞", layout="centered")
st.title(APP_TITLE)
st.caption("BGM/SEはオフ。台本オート生成に特化中。")

# サイドバー
with st.sidebar:
    st.header(APP_ID)
    st.session_state.persona = st.selectbox("顧客タイプ", list(PERSONAS.keys()),
                                            index=list(PERSONAS.keys()).index(st.session_state.persona))
    st.caption(PERSONAS[st.session_state.persona]["desc"])
    st.markdown("---")
    st.session_state.role = st.selectbox("立場", ["受付","担当者","決裁者"],
                                         index=["受付","担当者","決裁者"].index(st.session_state.role))
    st.session_state.industry = st.selectbox("業界", ["製造","IT","小売","サービス","その他"],
                                             index=["製造","IT","小売","サービス","その他"].index(st.session_state.industry))
    product  = st.text_input("商材（例：SaaS在庫管理）")
    calltime = st.selectbox("時間帯", ["朝","昼","夕方","夜"])

    st.markdown("---")
    st.subheader("📝 台本オート生成")
    source = st.radio("生成元", ["会話履歴から整形", "雛形から作成"], index=0)
    tone   = st.selectbox("トーン", ["丁寧・落ち着き", "ポップ・親しみ", "端的・ビジネスライク"], index=1)
    length = st.selectbox("ボリューム", ["短め", "ふつう", "やや長め"], index=1)
    gen_btn = st.button("台本を生成する", use_container_width=True)

# アバター（軽量版）
pre = preload_emotions(st.session_state.inst)
emo_now = emotion_from_progress(st.session_state.score, st.session_state.turns)
img_face = pre.get(emo_now) or pre.get("通常") or load_avatar(st.session_state.inst, emo_now)
if img_face is not None:
    st.image(img_face, width=420, caption=f"{st.session_state.inst}（{emo_now}）")

# 会話
st.subheader("💬 会話")
box = st.container()
with box:
    for m in st.session_state.chat:
        if m["role"]=="assistant":
            st.markdown(f"**顧客**：{m['content']}")
        else:
            st.markdown(f"<div style='text-align:right'>**あなた**：{m['content']}</div>", unsafe_allow_html=True)

# 入力
user_text=None
if not st.session_state.ended:
    user_text = st.chat_input("あなたの発話（Enterで送信）")

# 送信処理
if user_text and user_text.strip():
    txt=user_text.strip()
    st.session_state.chat.append({"role":"user","content":txt})
    st.session_state.turns = st.session_state.get("turns",0) + 1
    all_user=" ".join([m["content"] for m in st.session_state.chat if m["role"]=="user"])
    sc,dt = score_turn(all_user)
    st.session_state.score, st.session_state.detail = sc, dt
    with st.spinner("相手が応答中…"):
        reply, rt = ai_reply(st.session_state.persona, st.session_state.chat,
                             st.session_state.role, st.session_state.industry, product, calltime)
    st.session_state.last_rt=rt
    st.session_state.chat.append({"role":"assistant","content":reply})
    st.rerun()

# 操作
colA,colB,colC = st.columns([2,2,3])
with colA:
    end   = st.button("🎯 今日はここで採点", use_container_width=True)
with colB:
    ask   = st.button("🧑‍🏫 講評をもらう", use_container_width=True)
with colC:
    reset = st.button("🔁 やりなおす", use_container_width=True)

# KPI
st.subheader("📈 スコア")
st.write(f"総合：**{st.session_state.score} / {MAX_SCORE}**")
cols = st.columns(3)
for i,k in enumerate(CHECKS.keys()):
    with cols[i%3]:
        mark="🟢" if st.session_state.detail.get(k) else "⚪"
        st.markdown(f"{mark} {k}")
if st.session_state.get("last_rt") is not None:
    st.caption(f"直近の応答時間：{st.session_state.last_rt:.2f} 秒")

# 講評
if ask:
    with st.spinner("講評中…"):
        st.session_state.advice = get_advice(
            st.session_state.chat, st.session_state.score, st.session_state.detail,
            st.session_state.persona, st.session_state.inst, "ほめほめ"
        )
if st.session_state.get("advice"):
    st.markdown("### 🧑‍🏫 講評")
    st.markdown(st.session_state.advice)

# 採点保存/XP
def today_str(): return datetime.date.today().isoformat()
def now_iso():   return datetime.datetime.now().isoformat(timespec="seconds")
def save_jsonl(path: Path, rec: dict):
    with path.open("a", encoding="utf-8") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")

if end and not st.session_state.ended:
    st.session_state.ended=True
    ts = now_iso()
    path = DATA_DIR / f"calls_{today_str()}.jsonl"
    out = {
        "ts": ts,
        "persona": st.session_state.persona,
        "sim": {"role":st.session_state.role,"industry":st.session_state.industry,"product":product,"calltime":calltime},
        "score": st.session_state.score,
        "detail": st.session_state.detail,
        "history": st.session_state.chat
    }
    save_jsonl(path, out)
    p, leveled = add_xp(st.session_state.score)
    st.success(f"通話ログを保存しました：{path.name}（XP:{p['xp']} / Lv.{p['level']}）")

# リセット
if reset:
    st.session_state.chat=[{"role":"assistant","content":"はい、◯◯株式会社の（受付）です。ご用件をお願いします。"}]
    st.session_state.ended=False
    st.session_state.score=0
    st.session_state.detail={}
    st.session_state.turns=0
    st.session_state.advice=None
    st.session_state.show_congrats=False
    st.rerun()

# ---------- 台本オート生成：UI ----------
st.markdown("---")
st.header("📝 台本オート生成（Markdown/JSON保存つき）")

if gen_btn:
    sim_ctx = {
        "persona": st.session_state.persona,
        "role": st.session_state.role,
        "industry": st.session_state.industry,
        "product": product or "（未入力）",
        "calltime": calltime,
    }
    p = load_prog()
    level = p.get("level",0)
    score = st.session_state.score

    with st.spinner("台本を生成中…"):
        if source == "会話履歴から整形":
            doc = generate_script_with_openai(st.session_state.chat, sim_ctx, score, level, tone, length)
        else:
            doc = _mk_default_script(sim_ctx, score, level)

    md = script_to_markdown(doc)
    js = json.dumps(doc, ensure_ascii=False, indent=2)

    # 自動保存（scripts/日付/）
    day_dir = SCRIPTS_DIR / today_str()
    day_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%H%M%S")
    base = f"script_{ts}"
    (day_dir / f"{base}.md").write_text(md, encoding="utf-8")
    (day_dir / f"{base}.json").write_text(js, encoding="utf-8")

    st.success(f"台本を保存しました：scripts/{today_str()}/{base}.*")
    st.subheader("📄 プレビュー（Markdown）")
    st.markdown(md)

    st.download_button("⬇️ Markdownをダウンロード", data=md.encode("utf-8"), file_name=f"{base}.md", mime="text/markdown")
    st.download_button("⬇️ JSONをダウンロード", data=js.encode("utf-8"), file_name=f"{base}.json", mime="application/json")

    with st.expander("🔧 JSON（構造確認）", expanded=False):
        st.code(js, language="json")

# 便利見出し
st.markdown("#### 💡 使い方のコツ")
st.markdown("- 履歴から整形：いまの会話の**言い回しを活かした台本**が出ます。")
st.markdown("- 雛形から作成：**新規開拓前**に、業界×商材の汎用台本を先に作る用途に。")
st.markdown("- XPが上がるほど、雛形の数値やクロージング案が少しずつ“攻め”に寄ります。")
