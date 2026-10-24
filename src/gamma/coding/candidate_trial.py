"""A bounded, isolated QLoRA experiment. Never activates a production model."""
import argparse
import gc
import json
import os
import time
import uuid
from pathlib import Path

from .autonomous_store import safe_root, write_json, list_jobs, ACTIVE, execution_lock
from .candidate_data import collect, file_digest
from .candidate_evaluation import evaluate
from .candidate_policy import compare
from .learner import LocalPracticeLearner


def run(root, practice, models, endpoint, *, steps=12):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    root = safe_root(root)
    state = {'id': root.name, 'status': 'preparing', 'stage': 'dataset',
             'production_activated': False, 'model_weights_changed': False}
    def save(stage, **values):
        state.update(stage=stage, **values)
        write_json(root / 'state.json', state)
    def checkpoint():
        stop = root / 'stop'
        if stop.exists():
            raise RuntimeError('Owner stopped the candidate trial')
        if any(j['status'] in ACTIVE for j in list_jobs(root.parent.parent / 'autonomous')):
            raise RuntimeError('Owner coding work has priority; candidate stopped at a safe boundary')
    try:
        if type(steps) is not int or not 1 <= steps <= 50:
            raise ValueError('Invalid training step budget')
        checkpoint()
        LocalPracticeLearner(endpoint=endpoint, model='qwen3.8:27b').require_idle()
        receipt = json.loads((models / 'download.json').read_text())
        if receipt['status'] != 'ready' or receipt['checkpoint'] != 'Qwen/Qwen3-4B-Instruct-2507':
            raise RuntimeError('Verified official candidate download required')
        base = models / 'base'
        hashes = {name: file_digest(base / name) for name in receipt['weight_hashes']}
        if hashes != receipt['weight_hashes']:
            raise RuntimeError('Base checkpoint changed')
        samples = collect(practice)
        if len(samples) < 3:
            raise RuntimeError('At least three rechecked fictional examples required')
        write_json(root / 'dataset.json', {'samples': samples, 'private_content': False,
                                         'holdouts_included': False, 'purpose': 'hardware and pipeline trial'})
        save('loading', sample_count=len(samples), checkpoint=receipt['checkpoint'], revision=receipt['revision'])
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA training unavailable')
        free, total = torch.cuda.mem_get_info(0)
        if free < 12_000_000_000:
            raise RuntimeError('Insufficient free memory on training GPU')
        torch.manual_seed(54179)
        tokenizer = AutoTokenizer.from_pretrained(base, local_files_only=True, trust_remote_code=False)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(base, local_files_only=True,
            trust_remote_code=False, device_map={'': 0}, dtype=torch.bfloat16,
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True))
        def generate(conversation):
            checkpoint()
            model.eval()
            text = tokenizer.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
            tokens = tokenizer(text, return_tensors='pt').to('cuda:0')
            with torch.inference_mode():
                response = model.generate(**tokens, max_new_tokens=1024, do_sample=False,
                                          pad_token_id=tokenizer.pad_token_id)
            return tokenizer.decode(response[0, tokens.input_ids.shape[1]:], skip_special_tokens=True)
        save('baseline_evaluation')
        baseline = evaluate(generate, receipt['checkpoint'], receipt['revision'], root / 'baseline')
        write_json(root / 'baseline.json', baseline)
        checkpoint()
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
        model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05,
            target_modules=['q_proj','k_proj','v_proj','o_proj'], bias='none', task_type='CAUSAL_LM'))
        trainable = [(name, p) for name, p in model.named_parameters() if p.requires_grad]
        if not trainable or any('lora_' not in name for name, _ in trainable):
            raise RuntimeError('Base parameters must remain frozen')
        before = {name: p.detach().float().cpu().clone() for name, p in trainable}
        encoded = []
        for sample in samples:
            conversation = sample['messages']
            prefix = tokenizer.apply_chat_template(conversation[:-1], tokenize=False, add_generation_prompt=True)
            full = tokenizer.apply_chat_template(conversation, tokenize=False, add_generation_prompt=False)
            prefix_ids = tokenizer(prefix, add_special_tokens=False)['input_ids']
            ids = tokenizer(full, add_special_tokens=False)['input_ids']
            if not 0 < len(prefix_ids) < len(ids) <= 1536 or ids[:len(prefix_ids)] != prefix_ids:
                raise RuntimeError('Training token alignment or context budget failed')
            labels = [-100] * len(prefix_ids) + ids[len(prefix_ids):]
            encoded.append((ids, labels))
        optimizer = torch.optim.AdamW([p for _, p in trainable], lr=0.0001)
        model.config.use_cache = False
        model.train()
        losses = []
        save('training', status='running', trainable_parameters=sum(p.numel() for _, p in trainable),
             base_parameters_frozen=True, steps_completed=0)
        for index in range(steps):
            checkpoint()
            ids, labels = encoded[index % len(encoded)]
            optimizer.zero_grad(set_to_none=True)
            input_ids = torch.tensor([ids], device='cuda:0')
            output = model(input_ids=input_ids, attention_mask=torch.ones_like(input_ids),
                           labels=torch.tensor([labels], device='cuda:0'))
            loss = output.loss
            if not torch.isfinite(loss):
                raise RuntimeError('Nonfinite training loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for _, p in trainable], 1.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
            save('training', steps_completed=index + 1)
        changed = any(not torch.equal(before[name], p.detach().float().cpu()) for name, p in trainable)
        if not changed:
            raise RuntimeError('No adapter parameters changed')
        adapter = root / 'adapter'
        model.save_pretrained(adapter, safe_serialization=True)
        adapter_hash = file_digest(adapter / 'adapter_model.safetensors')
        write_json(root / 'training.json', {'adapter_sha256': adapter_hash, 'steps': steps,
            'losses': losses, 'adapter_parameters_changed': True, 'base_parameters_frozen': True,
            'quality_demonstrated': False, 'torch_version': torch.__version__})
        save('candidate_evaluation', model_weights_changed=True, adapter_sha256=adapter_hash)
        model.config.use_cache = True
        model.gradient_checkpointing_disable()
        del optimizer, before
        gc.collect()
        torch.cuda.empty_cache()
        candidate = evaluate(generate, receipt['checkpoint'], receipt['revision'], root / 'candidate')
        candidate['adapter_sha256'] = adapter_hash
        write_json(root / 'candidate.json', candidate)
        decision = compare(baseline, candidate)
        unchanged = {name: file_digest(base / name) for name in hashes} == hashes
        decision.update(base_files_unchanged=unchanged, production_activated=False)
        if not unchanged:
            decision.update(passed=False, reason='Base files changed')
        write_json(root / 'decision.json', decision)
        # Even a passing 4B pilot cannot replace the incumbent 27B on this evidence.
        save('completed', status='candidate_passed' if decision['passed'] else 'candidate_rejected',
             decision=decision, incumbent_comparison_required=True,
             baseline_passes=sum(t['passed'] for t in baseline['tasks']),
             candidate_passes=sum(t['passed'] for t in candidate['tasks']))
    except Exception as exc:
        save('stopped', status='blocked', error=type(exc).__name__, reason=str(exc)[:250])
    return state


def main():
    os.umask(0o077)
    from ..config import settings
    parser = argparse.ArgumentParser()
    parser.add_argument('--steps', type=int, default=12)
    args = parser.parse_args()
    coding = settings.data_dir / 'coding'
    root = coding / 'candidates' / ('candidate-' + uuid.uuid4().hex)
    with execution_lock(coding / 'candidates'):
        print(json.dumps(run(root, coding / 'practice', coding / 'models' / 'qwen3-4b-instruct-2507',
                             settings.local_llm_endpoint, steps=args.steps)))


if __name__ == '__main__':
    main()
