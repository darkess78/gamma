"""Delivery controls preserve the tuned voice and canonical continuity."""
import array
import io
import json
import math
import shutil
import wave

import pytest
from pydantic import ValidationError
from gamma.coordination.personal_models import PersonalStateUpdates, PrivateTurnDraft
from gamma.coordination.turn_schema import private_turn_schema
from gamma.schemas.response import AssistantResponse, ConversationTurnDraft
from gamma.voice.delivery_audio import transform_wave
from test_coordination_core import make_core
from test_private_interaction import setup, observation


def pcm_wave(values, rate=24000):
    data = array.array('h', values)
    import sys
    if sys.byteorder != 'little':
        data.byteswap()
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        w.writeframes(data.tobytes())
    return out.getvalue()


def decode(raw):
    with wave.open(io.BytesIO(raw), 'rb') as w:
        values = array.array('h', w.readframes(w.getnframes()))
        import sys
        if sys.byteorder != 'little':
            values.byteswap()
        return values, w.getframerate()


@pytest.mark.parametrize('field,bad', [('voice_pace', v) for v in (.89,1.26,True,'1',float('nan'),float('inf'))]
    + [('voice_loudness_db',v) for v in (-5.1,5.1,False,'0',float('nan'),float('-inf'))])
def test_typed_controls_reject_invalid_and_nonfinite_values(field,bad):
    with pytest.raises(ValidationError):
        PersonalStateUpdates(**{field:bad})
    with pytest.raises(ValueError):
        transform_wave(pcm_wave([1,2]), **{('pace' if field=='voice_pace' else 'loudness_db'):bad})


def test_public_contract_and_pitch_are_unchanged():
    public = ConversationTurnDraft.model_json_schema()['$defs']['BoundedStateUpdates']['properties']
    assert 'voice_pace' not in public and 'voice_loudness_db' not in public
    with pytest.raises(ValidationError):
        PrivateTurnDraft(state_updates={'voice_pitch':1})
    props = private_turn_schema()['$defs']['PersonalStateUpdates']['properties']
    assert props['voice_pace']['anyOf'][0]['minimum']==.90
    assert props['voice_pace']['anyOf'][0]['maximum']==1.25


def test_exact_bypass_linear_level_and_uniform_headroom():
    raw = pcm_wave([0,1000,-2000,0])
    assert transform_wave(raw)[0] is raw
    lower, meta = transform_wave(raw,loudness_db=-5)
    assert list(decode(lower)[0])==[0,562,-1125,0]
    assert meta['applied_loudness_db']==-5
    loud, meta = transform_wave(pcm_wave([30000,-30000,15000]),loudness_db=5)
    samples,_=decode(loud)
    assert max(map(abs,samples))<=31130 and abs(samples[0]/samples[2]-2)<.001
    assert meta['headroom_limited'] and meta['applied_loudness_db']<5
    with pytest.raises(ValueError):
        transform_wave(raw[:-1])


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='ffmpeg required for actual tempo integration')
@pytest.mark.parametrize('pace', [.90,1.25])
def test_real_tempo_changes_duration_preserves_pitch_and_matches_level(pace):
    raw=pcm_wave(round(13000*math.sin(2*math.pi*400*i/24000)) for i in range(48000))
    out,meta=transform_wave(raw,pace=pace)
    samples,rate=decode(out)
    assert abs(len(samples)/rate-2/pace)<.1
    crossings=sum(a<=0<b for a,b in zip(samples,samples[1:]))
    assert abs(crossings/(len(samples)/rate)-400)<3
    original,_=decode(raw)
    rms=lambda v:math.sqrt(sum(x*x for x in v)/len(v))
    assert abs(20*math.log10(rms(samples)/rms(original)))<.01
    assert not meta['headroom_limited']


def test_absolute_choices_inherit_across_turns_restart_and_explicit_reset(tmp_path):
    seen=[]
    updates=iter([{'voice_pace':1.25,'voice_loudness_db':-5},{},{'voice_pace':1.1},{'voice_pace':1.,'voice_loudness_db':0.}])
    def draft(text, context):
        seen.append(json.loads(context)['voice_delivery'])
        return AssistantResponse(display_text='Still here.',delivery_mode='text_only',
            state_updates=PersonalStateUpdates(**next(updates)))
    core=make_core(tmp_path,draft=draft)
    try:
        for i in range(2):core.input('Hello','private',str(i))
        assert core.status()['voice_delivery']=={'pace':1.25,'loudness_db':-5}
    finally:core.close()
    core=make_core(tmp_path,draft=draft)
    try:
        assert core.status()['voice_delivery']=={'pace':1.25,'loudness_db':-5}
        core.input('Hello','private','2')
        assert core.status()['voice_delivery']=={'pace':1.1,'loudness_db':-5}
        core.input('Hello','private','3')
        assert core.status()['voice_delivery']=={'pace':1.,'loudness_db':0.}
        assert seen[0]=={'pace':1.,'loudness_db':0.}
        assert seen[1:3]==[{'pace':1.25,'loudness_db':-5}]*2
    finally:core.close()


def test_one_choice_reaches_all_chunks_without_sentence_reset(tmp_path,monkeypatch):
    captures=[]
    def effect(raw,**options):
        captures.append(options)
        return raw,{'pace':options['pace'],'requested_loudness_db':options['loudness_db']}
    monkeypatch.setattr('gamma.voice.delivery_audio.transform_wave',effect)
    text='First complete sentence carries the useful point. Second complete sentence adds a little detail.'
    core,service=setup(tmp_path,draft=lambda *_:AssistantResponse(display_text=text,delivery_mode='text_only',
        state_updates=PersonalStateUpdates(voice_pace=.9,voice_loudness_db=3)))
    try:
        result=service.observe(observation())
        assert result['status']=='generated' and len(captures)==2
        assert captures==[{'pace':.9,'loudness_db':3}]*2
        assert core.status()['voice_delivery']=={'pace':.9,'loudness_db':3}
    finally:service.close();core.close()
