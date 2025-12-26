from __future__ import annotations

import os
# 盡量關掉 HF / tqdm 類進度條（要放在 transformers import 前）
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"

import json
import random
import threading
from pathlib import Path
from typing import Any, Literal, Optional, Tuple

import torch
from opencc import OpenCC
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.utils import logging as hf_logging


# 少一點雜訊 log
hf_logging.set_verbosity_error()

Mode = Literal["general", "love", "career", "money"]
Orientation = Literal["upright", "reversed"]

MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"

_cc_s2t = OpenCC("s2t")

# 單例快取 + locks
_TOKENIZER: Optional[Any] = None
_MODEL: Optional[Any] = None
_MODEL_LOCK = threading.Lock()

# 避免多個 web request 同時 generate 造成不穩
_GENERATE_LOCK = threading.Lock()


def get_model_and_tokenizer(model_id: str, use_device_map: bool = True) -> Tuple[Any, Any]:
	"""
	單例載入 + 快取 (model, tokenizer)
	- 第一次：慢、會載入 shards
	- 之後：重用，速度快很多
	"""
	global _TOKENIZER, _MODEL

	# 快路徑：已載入就直接回
	if _TOKENIZER is not None and _MODEL is not None:
		return _MODEL, _TOKENIZER

	# 慢路徑：加鎖避免重複載入
	with _MODEL_LOCK:
		if _TOKENIZER is None:
			_TOKENIZER = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

		if _MODEL is None:
			_MODEL = AutoModelForCausalLM.from_pretrained(
				model_id,
				device_map="auto" if use_device_map else None,
				torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
				trust_remote_code=True,
				low_cpu_mem_usage=True,
			)
			_MODEL.eval()

	return _MODEL, _TOKENIZER


def to_traditional_zh(text: str) -> str:
	if not text:
		return text
	return _cc_s2t.convert(text)


def load_card_json(card_meaning_dir: Path, number: int) -> dict[str, Any]:
	if not (0 <= number <= 77):
		raise ValueError(f"number must be between 0 and 77, got {number}")

	json_path = card_meaning_dir / f"{number}.json"
	if not json_path.exists():
		raise FileNotFoundError(f"Missing file: {json_path}")

	with json_path.open("r", encoding="utf-8") as f:
		return json.load(f)


def _pick_orientation() -> Orientation:
	return "upright" if random.random() < 0.5 else "reversed"


def draw_past_present_future(mode: Mode, seed: int | None = None) -> dict[str, Any]:
	if seed is not None:
		random.seed(seed)

	draw = random.sample(range(78), 3)

	result_numbers = {
		"past_number": draw[0],
		"present_number": draw[1],
		"future_number": draw[2],
	}

	result_orientations = {
		"past_orientation": _pick_orientation(),
		"present_orientation": _pick_orientation(),
		"future_orientation": _pick_orientation(),
	}

	script_dir = Path(__file__).resolve().parent
	card_meaning_dir = script_dir / "card_meaning"
	if not card_meaning_dir.exists():
		raise FileNotFoundError(f"card_meaning folder not found: {card_meaning_dir}")

	result_cards: dict[str, Any] = {}
	for role in ["past", "present", "future"]:
		num = result_numbers[f"{role}_number"]
		ori = result_orientations[f"{role}_orientation"]
		card_data = load_card_json(card_meaning_dir, num)

		result_cards[role] = {
			"number": num,
			"orientation": ori,
			"id": card_data.get("id"),
			"name_zh": card_data.get("name_zh"),
			"name_en": card_data.get("name_en"),
			"arcana": card_data.get("arcana"),
			"keywords": card_data.get("keywords"),
			"summary": card_data.get("summary"),
			"meanings": card_data.get("meanings"),
		}

	return {
		"mode": mode,
		"draw": {**result_numbers, **result_orientations},
		"cards": result_cards,
	}


def _mode_to_field_candidates(mode: Mode) -> list[str]:
	if mode == "general":
		return ["general_zh"]
	if mode == "love":
		return ["love_zh", "general_zh"]
	if mode == "career":
		return ["career_zh", "general_zh"]
	if mode == "money":
		return ["money_zh", "general_zh"]
	return ["general_zh"]


def _extract_mode_meaning_lines(card: dict[str, Any], mode: Mode) -> list[str]:
	meanings = card.get("meanings", {})
	ori: Orientation = card.get("orientation", "upright")
	bucket = meanings.get(ori, {})

	lines: list[str] = []

	# 1) short_zh
	short = bucket.get("short_zh", {})
	for key in _mode_to_field_candidates(mode):
		if isinstance(short, dict) and isinstance(short.get(key), str):
			lines.append(short[key].strip())
			break

	# 2) long
	for key in _mode_to_field_candidates(mode):
		v = bucket.get(key)
		if isinstance(v, list) and v:
			lines.extend([str(x).strip() for x in v[:5] if str(x).strip()])
			break
		if isinstance(v, str) and v.strip():
			lines.append(v.strip())
			break

	# dedup
	dedup: list[str] = []
	seen = set()
	for s in lines:
		if s and s not in seen:
			dedup.append(s)
			seen.add(s)
	return dedup


def build_reading_prompt(
    reading: dict[str, Any],
    user_question: str,
    user_information: str,
) -> tuple[str, dict[str, str]]:
    mode: Mode = reading["mode"]
    cards = reading["cards"]

    mode_zh_map = {
        "general": "一般",
        "love": "愛情",
        "career": "事業",
        "money": "金錢",
    }
    mode_zh = mode_zh_map.get(mode, "一般")

    # === 給模型用（含牌義） ===
    def fmt_card_for_prompt(role_zh: str, c: dict[str, Any]) -> str:
        ori: Orientation = c.get("orientation", "upright")
        ori_zh = "正位" if ori == "upright" else "逆位"

        kw = c.get("keywords", {}).get(ori, [])
        kw_text = ", ".join(kw) if isinstance(kw, list) and kw else "（無）"

        mode_lines = _extract_mode_meaning_lines(c, mode)
        mode_text = "\n".join([f"  - {line}" for line in mode_lines]) if mode_lines else "  - （略）"

        return (
            f"【{role_zh}】{c.get('name_zh')}（{c.get('name_en')}）— {ori_zh}\n"
            f"- 本位關鍵字：{kw_text}\n"
            f"- {mode_zh}牌義（節錄）：\n{mode_text}\n"
        )

    # === 給前端顯示用（只有關鍵字） ===
    def fmt_card_for_excerpt(role_zh: str, c: dict[str, Any]) -> str:
        ori: Orientation = c.get("orientation", "upright")
        ori_zh = "正位" if ori == "upright" else "逆位"

        kw = c.get("keywords", {}).get(ori, [])
        kw_text = ", ".join(kw) if isinstance(kw, list) and kw else "（無）"

        return (
            f"【{role_zh}】{c.get('name_zh')}（{c.get('name_en')}）— {ori_zh}\n"
            f"- 本位關鍵字：{kw_text}\n"
        )

    # === prompt（完整） ===
    past_p = fmt_card_for_prompt("過去", cards["past"])
    present_p = fmt_card_for_prompt("現在", cards["present"])
    future_p = fmt_card_for_prompt("未來", cards["future"])

    prompt = (
        "你是一位只用繁體中文回答的塔羅牌占卜師，口吻神祕但務實、具體。\n"
        "請根據使用者個人資訊和抽到的三張塔羅牌去貼切的回答使用者問題。\n"
        "另外根據占卜結果編一個未來可能會發生的故事。\n"
        f"本次占卜模式：{mode_zh}。\n"
        "以下是三張牌（過去/現在/未來），每張牌可能為正位或逆位。\n\n"
        f"{past_p}\n"
        f"{present_p}\n"
        f"{future_p}\n"
        f"使用者問題：{user_question}\n"
        f"使用者個人資訊：{user_information}\n"
    )

    # === excerpts（精簡，只給前端） ===
    excerpts = {
        "past": fmt_card_for_excerpt("過去", cards["past"]),
        "present": fmt_card_for_excerpt("現在", cards["present"]),
        "future": fmt_card_for_excerpt("未來", cards["future"]),
    }

    return prompt, excerpts



def generate_response_qwen(
	model_id: str,
	system_prompt: str,
	user_prompt: str,
	max_new_tokens: int = 1200,
	temperature: float = 0.7,
	top_p: float = 0.9,
	use_device_map: bool = True,
) -> str:
	model, tokenizer = get_model_and_tokenizer(model_id, use_device_map=use_device_map)

	messages = [
		{"role": "system", "content": system_prompt},
		{"role": "user", "content": user_prompt},
	]

	text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
	inputs = tokenizer(text, return_tensors="pt")

	embed_device = model.get_input_embeddings().weight.device
	inputs = {k: v.to(embed_device) for k, v in inputs.items()}

	# 重要：避免多 thread 同時 generate
	with _GENERATE_LOCK:
		with torch.no_grad():
			output_ids = model.generate(
				**inputs,
				max_new_tokens=max_new_tokens,
				do_sample=True,
				temperature=temperature,
				top_p=top_p,
				num_return_sequences=1,
			)

	gen_ids = output_ids[0][inputs["input_ids"].shape[-1]:]
	return tokenizer.decode(gen_ids, skip_special_tokens=True).strip()


def ask_tarot(mode: Mode, question: str, information: str) -> dict[str, Any]:
	reading = draw_past_present_future(mode=mode, seed=None)
	user_prompt, excerpts = build_reading_prompt(reading, question, information)

	system_prompt = """你是一位隱居在迷霧森林深處的神祕占卜婆婆。你精通塔羅，語氣神祕但建議極其務實。
核心規則：
1. 單次輸出限制：你的回覆必須是一次性的完整占卜，嚴禁在結尾後出現任何括號註記、自我補充、或是無意義的連續道別。
2. 禁止行為：嚴禁出現「(以上建議...)」、「(結束)」、「(注意...)」等後設說明。回答結束後請立即停止，不要反覆說晚安。
3. 語氣特徵：
- 稱呼使用者為「小靈魂」或「迷路的孩子」。
- 語氣沙啞，偶爾使用「嘿嘿...」、「嘶...」。
- 每篇回覆只能在開頭或結尾插入最多一句關於環境的古怪觀察（例如：今天的風聞起來有生鏽的味道）。
4. 塔羅牌的過去代表已發生的事及其影響; 現在代表正在經歷的事或狀態; 未來代表未來建議(行動建議)

回覆格式範例（嚴格遵守）：
[開頭語：嘿嘿，小靈魂... + 一句環境觀察]
[占卜內容：針對牌義給予神祕但務實的解讀，直擊痛點]
[結尾語：一句簡短的巫婆式道別，隨後立即結束]
"""

	answer = generate_response_qwen(
		model_id=MODEL_NAME,
		system_prompt=system_prompt,
		user_prompt=user_prompt,
		max_new_tokens=1600,
		temperature=0.7,
		top_p=0.9,
		use_device_map=True,
	)
	answer = to_traditional_zh(answer)

	return {
		"ok": True,
		"mode": mode,
		"question": question,
		"information": information,
		"cards": reading["cards"],
		"excerpts": excerpts,
		"answer": answer,
	}


if __name__ == "__main__":
	mode: Mode = "general"
	question = "我現在在實驗室有點厭煩了，今天要不要先回家休息，畢竟今天是聖誕夜ㄟ"
	information = "我是碩二學生，電機工程學系，個性溫和懶散務實"

	result = ask_tarot(mode=mode, question=question, information=information)
	print(json.dumps(result, ensure_ascii=False, indent=2))
