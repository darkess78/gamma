"""Complete staged Nginx gate: disposable trees and fake syntax commands only."""
import json
from pathlib import Path

import pytest

from test_review_proxy_lease import lease, prepared, Clock


def tree(tmp_path):
    root = tmp_path / 'nginx'
    root.mkdir()
    confd = root / 'conf.d'
    entries, packet = prepared(confd)
    for name in ('modules-enabled', 'modules-available', 'sites-enabled', 'sites-available'):
        (root / name).mkdir()
    module = root / 'modules-available/extra.conf'
    module.write_text('load_module /usr/lib/nginx/modules/example.so;\n')
    (root / 'modules-enabled/extra.conf').symlink_to(module)
    snippet = root / 'shared-snippet.conf'
    snippet.write_text('keepalive_timeout 65;\n')
    site = root / 'sites-available/unchanged.conf'
    site.write_text('include "' + str(snippet) + '";\n')
    (root / 'sites-enabled/site.conf').symlink_to(site)
    extra = confd / 'unrelated.conf'
    extra.write_text('map $value $result { default 1; }\n')
    mime = root / 'mime.types'; mime.write_text('types { text/plain txt; }\n')
    master = root / 'nginx.conf'
    original = ('user www-data;\nworker_processes auto;\npid /run/nginx.pid;\n'
        'include ' + str(root / 'modules-enabled/*.conf') + ';\n'
        'events { worker_connections 768; }\nhttp {\n'
        ' include ' + str(mime) + ';\n'
        ' include ' + str(confd / '*.conf') + ';\n'
        ' include ' + str(root / 'sites-enabled/*') + ';\n}\n')
    master.write_text(original)
    return entries, master, root, snippet, extra


def test_complete_stage_preserves_master_modules_sites_and_every_other_confd_include(tmp_path):
    entries, master, root, snippet, extra = tree(tmp_path)
    original = master.read_text()
    stage = lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    directive = next(d for d in lease.include_directives(original) if d['path'] == str(root / 'conf.d/*.conf'))
    rendered = []
    for path in sorted((root / 'conf.d').glob('*.conf')):
        selected = stage.folder / (path.name + '.after') if path in {e['path'] for e in entries} else path
        rendered.append('include "' + str(selected) + '";')
    assert stage.complete.read_text() == original[:directive['start']] + '\n'.join(rendered) + original[directive['end']:]
    assert 'user www-data;' in stage.complete.read_text() and 'pid /run/nginx.pid;' in stage.complete.read_text()
    assert str(root / 'modules-enabled/*.conf') in stage.complete.read_text()
    assert str(root / 'sites-enabled/*') in stage.complete.read_text()
    assert str(extra) in stage.inputs and str(snippet) in stage.inputs
    assert stage.inputs[str(root / 'modules-enabled/extra.conf')]['resolved'] == str(root / 'modules-available/extra.conf')
    assert all(e['path'].read_bytes() == e['before'] for e in entries)
    assert json.loads((stage.folder / 'stage-proof.json').read_text())['syntax_validated'] is False


def controller(stage, entries, tmp_path, staged_runner, *, writer=None):
    clock, calls = Clock(), []
    def recorded_writer(path, expected, new):
        calls.append('write:' + path.name)
        (writer or lease.atomic_cas)(path, expected, new)
    result = lease.LeaseController(entries, tmp_path / 'journal.json', probe=lambda: calls.append('probe'),
        staged_preflight=lambda: stage.preflight(staged_runner), syntax=lambda: calls.append('live-syntax'),
        reload=lambda: calls.append('reload'), writer=recorded_writer, clock=clock, sleep=clock.sleep, ttl=2)
    return result, calls


def test_complete_staged_syntax_failure_guarantees_zero_live_writes_and_zero_reload(tmp_path):
    entries, master, *_ = tree(tmp_path)
    stage = lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    commands = []
    def syntax(argv):
        commands.append(argv)
        raise RuntimeError('complete_staged_configuration_invalid')
    service, calls = controller(stage, entries, tmp_path, syntax)
    assert not service.run()
    assert commands == [['/usr/sbin/nginx', '-t', '-c', str(stage.complete)]]
    assert not any(c.startswith('write:') for c in calls)
    assert 'reload' not in calls and 'live-syntax' not in calls
    assert all(e['path'].read_bytes() == e['before'] for e in entries)
    assert stage.proof['syntax_validated'] is False


def test_installation_is_after_staged_validation_and_each_reload_has_fresh_live_syntax(tmp_path):
    entries, master, *_ = tree(tmp_path)
    stage = lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    staged = []
    service, calls = controller(stage, entries, tmp_path, lambda argv: staged.append(argv))
    assert service.run()
    assert staged == [['/usr/sbin/nginx', '-t', '-c', str(stage.complete)]]
    assert service.record['staged_preflight_completed'] is True
    for i, call in enumerate(calls):
        if call == 'reload': assert calls[i - 1] == 'live-syntax'
    assert service.record['history'][0]['phase'] == 'validating_complete_staged_config'
    assert all(e['path'].read_bytes() == e['before'] for e in entries)


@pytest.mark.parametrize('change', ['master', 'include-bytes', 'wildcard-membership', 'symlink-target', 'staged-after', 'staged-master'])
def test_complete_stage_drift_during_validation_blocks_all_live_mutation(tmp_path, change):
    entries, master, root, snippet, extra = tree(tmp_path)
    stage = lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    def syntax(_):
        if change == 'master': master.write_text(master.read_text() + '# independent master edit\n')
        elif change == 'include-bytes': snippet.write_text('keepalive_timeout 60;\n')
        elif change == 'wildcard-membership': (root / 'sites-enabled/new.conf').write_text('# new included site\n')
        elif change == 'symlink-target':
            alternative = root / 'modules-available/replacement.conf'
            alternative.write_text((root / 'modules-available/extra.conf').read_text())
            path = root / 'modules-enabled/extra.conf'; path.unlink(); path.symlink_to(alternative)
        elif change == 'staged-after': (stage.folder / (entries[0]['path'].name + '.after')).write_text('invalid stage')
        else: stage.complete.write_text(stage.complete.read_text() + '# altered staged master\n')
    service, calls = controller(stage, entries, tmp_path, syntax)
    assert not service.run()
    assert not any(c.startswith('write:') for c in calls) and 'reload' not in calls
    assert all(e['path'].read_bytes() == e['before'] for e in entries)


def test_drift_after_successful_stage_callback_but_before_install_is_rechecked(tmp_path):
    entries, master, root, *_ = tree(tmp_path)
    stage = lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    service, calls = controller(stage, entries, tmp_path, lambda _: None)
    def validate_then_change():
        guard = stage.preflight(lambda _: None)
        (root / 'conf.d/new.conf').write_text('# arrived after validation\n')
        return guard
    service.staged_preflight = validate_then_change
    assert not service.run() and stage.proof['syntax_validated']
    assert not any(c.startswith('write:') for c in calls) and 'reload' not in calls


@pytest.mark.parametrize('when', ['candidate-probe', 'live-syntax'])
def test_drift_during_last_readiness_or_syntax_check_still_prevents_initial_reload(tmp_path, when):
    entries, master, *_ = tree(tmp_path)
    stage = lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    service, calls = controller(stage, entries, tmp_path, lambda _: None)
    counts = {'probe': 0, 'syntax': 0}
    def probe():
        counts['probe'] += 1
        if when == 'candidate-probe' and counts['probe'] == 2:
            master.write_text(master.read_text() + '# edit during final readiness\n')
    def syntax():
        counts['syntax'] += 1; calls.append('live-syntax')
        if when == 'live-syntax' and counts['syntax'] == 1:
            master.write_text(master.read_text() + '# edit during final syntax\n')
    service.probe, service.syntax = probe, syntax
    assert not service.run() and service.record['phase'] == 'cleaned'
    assert calls.count('reload') == 1  # Cleanup only, after a fresh live syntax check.
    assert all(e['path'].read_bytes() == e['before'] for e in entries)


@pytest.mark.parametrize('after_write', [1, 2])
def test_drift_during_install_aborts_before_activation_and_preserves_unrelated_edit(tmp_path, after_write):
    entries, master, *_ = tree(tmp_path)
    stage = lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    writes = 0
    def writer(path, expected, new):
        nonlocal writes
        lease.atomic_cas(path, expected, new)
        writes += 1
        if writes == after_write:
            master.write_text(master.read_text() + '# new owner master edit\n')
    service, calls = controller(stage, entries, tmp_path, lambda _: None, writer=writer)
    assert not service.run() and service.record['phase'] == 'cleaned'
    assert '# new owner master edit' in master.read_text()
    assert all(e['path'].read_bytes() == e['before'] for e in entries)
    assert calls.count('reload') == 1  # Cleanup only; proposed routing was not activated.


@pytest.mark.parametrize('problem', ['missing-confd', 'duplicate-confd', 'relative-include', 'dynamic-include', 'include-cycle'])
def test_incomplete_or_unsupported_master_scope_is_rejected_before_stage_mutation(tmp_path, problem):
    entries, master, root, snippet, _ = tree(tmp_path)
    confd = str(root / 'conf.d/*.conf')
    if problem == 'missing-confd': master.write_text(master.read_text().replace('include ' + confd + ';', ''))
    elif problem == 'duplicate-confd': master.write_text(master.read_text() + '\ninclude ' + confd + ';\n')
    elif problem == 'relative-include': master.write_text(master.read_text() + '\ninclude relative.conf;\n')
    elif problem == 'dynamic-include': master.write_text(master.read_text() + '\ninclude $runtime;\n')
    else: snippet.write_text('include ' + str(master) + ';\n')
    with pytest.raises(ValueError): lease.CompleteNginxStage(entries, tmp_path / 'stage', master=master)
    assert not (tmp_path / 'stage').exists()
    assert all(e['path'].read_bytes() == e['before'] for e in entries)


def test_include_lexer_tracks_real_spans_and_does_not_parse_quoted_or_escaped_fake_directives():
    value = 'http { set $x "include /fake;"; set $x a\\;include /stillfake; include#comment\n"/real#path"; }'
    rows = lease.include_directives(value)
    assert [r['path'] for r in rows] == ['/real#path']
    assert value[rows[0]['start']:rows[0]['end']] == 'include#comment\n"/real#path";'
    for unsupported in ['include $dynamic;', 'include "unterminated;', 'include /a /b;', '"include" /a;', 'include /a\\ b;']:
        with pytest.raises(ValueError): lease.include_directives(unsupported)
