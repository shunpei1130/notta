import copy
import os
import unittest
from unittest.mock import patch

import test_workspace as fixtures
initial = fixtures.initial
import server as s
import service_workspace as work
import service_intake as intake
import workspace_planning as plan


class PlanningTest(unittest.TestCase):
    user = fixtures.WorkspaceTest.user
    problem = fixtures.WorkspaceTest.problem
    request = fixtures.WorkspaceTest.request
    get = fixtures.WorkspaceTest.get
    edit = fixtures.WorkspaceTest.edit
    output = fixtures.WorkspaceTest.output

    def setUp(self):
        fixtures.WorkspaceTest.setUp(self)
        self.planning_flags = patch.dict(os.environ, {'NOTTA_PLANNING_ENABLED': 'yes'})
        self.planning_flags.start()

    def tearDown(self):
        fixtures.WorkspaceTest.tearDown(self)

    def prepared(self):
        d = copy.deepcopy(self.get()['draft'])
        stages = [{'id': s.ident(), 'title': v, 'outcome': '次に進める準備が整う'} for v in ('条件を整理', '制作と準備', '実施と振り返り')]
        d['roadmap'] = {'goal': '架空の写真展を実施して片付けまで終える', 'criteria': '展示・交流・会場返却・お礼が完了している', 'stages': stages}
        for i, t in enumerate(d['tasks']):
            t.update(stageId=stages[min(i//5, 2)]['id'], dependsOn=[d['tasks'][i-1]['id']] if i>1 else [], useCommonTerms=True)
        d['public'] = {'title': '公開する写真展', 'overview': 'ファンと一緒につくる写真展', 'schedule': '時期と会場は未定。会場探しから協力者を募集します。',
                       'conditions': '協力は任意。実費・参加費・権利は応募前の案内に沿って相談します。', 'recruitment': '写真と展示を一緒につくりたい方を募集しています。'}
        return self.edit(d)

    def publish(self):
        w = self.prepared()
        return work.publish(self.c, self.uid, self.pid, {'version': w['version'], 'consent': True, 'confirmRoles': True})

    def test_missing_legacy_roadmap_is_not_fabricated(self):
        w = self.get()
        self.assertIsNone(w['draft']['roadmap'])
        self.assertFalse(w['roadmapProgress']['ready'])
        self.assertEqual(w['roadmapProgress']['nextTaskIds'], [])

    def test_roadmap_progress_next_tasks_and_parallel_roots_use_actual_state(self):
        w = self.prepared();ids = [t['id'] for t in w['draft']['tasks']]
        self.assertTrue(w['roadmapProgress']['ready'])
        self.assertEqual(w['roadmapProgress']['done'], 0)
        self.assertEqual(w['roadmapProgress']['nextTaskIds'], ids[:2])
        d = w['draft'];d['tasks'][1]['status'] = '完了'
        w = self.edit(d)
        self.assertEqual(w['roadmapProgress']['done'], 1)
        self.assertIn(ids[2], w['roadmapProgress']['nextTaskIds'])
        self.assertNotIn(ids[3], w['roadmapProgress']['nextTaskIds'])

    def test_cycles_missing_references_self_references_and_duplicate_stages_are_rejected(self):
        w = self.prepared()
        for mutation in ('cycle', 'unknown', 'self', 'stage'):
            d = copy.deepcopy(w['draft'])
            if mutation == 'cycle':
                d['tasks'][0]['dependsOn'] = [d['tasks'][1]['id']];d['tasks'][1]['dependsOn'] = [d['tasks'][0]['id']]
            elif mutation == 'unknown': d['tasks'][0]['dependsOn'] = [s.ident()]
            elif mutation == 'self': d['tasks'][0]['dependsOn'] = [d['tasks'][0]['id']]
            else: d['roadmap']['stages'].append(copy.deepcopy(d['roadmap']['stages'][0]))
            self.problem(400, lambda: self.edit(d))
        self.assertEqual(self.get()['draft'], w['draft'])

    def test_fifty_connected_tasks_preserve_ids(self):
        w = self.prepared();d = w['draft']
        for i in range(36):
            task = copy.deepcopy(d['tasks'][-1]);task.update(id=s.ident(), title='追加作業'+str(i), dependsOn=[d['tasks'][-1]['id']]);d['tasks'].append(task)
        w = self.edit(d)
        self.assertEqual(len(w['draft']['tasks']), 50)
        self.assertTrue(w['roadmapProgress']['ready'])
        self.assertEqual([t['id'] for t in w['draft']['tasks']], [t['id'] for t in d['tasks']])

    def test_old_ui_and_disabled_new_ui_do_not_erase_metadata(self):
        w = self.prepared();old = copy.deepcopy(w['draft']);old.pop('roadmap')
        for task in old['tasks']:
            for key in ('stageId', 'dependsOn', 'recruitCandidate', 'useCommonTerms'): task.pop(key, None)
        old['overview'] = '以前の画面で本文を更新'
        with patch.dict(os.environ, {'NOTTA_PLANNING_ENABLED': 'no'}):
            result = self.edit(old)
        self.assertEqual(result['draft']['roadmap'], w['draft']['roadmap'])
        self.assertEqual(result['draft']['tasks'], w['draft']['tasks'])

    def test_owner_confirms_roles_once_and_public_data_excludes_private_graph(self):
        w = self.prepared()
        self.assertTrue(w['recruitmentReadiness']['ready'])
        self.problem(400, lambda: work.publish(self.c, self.uid, self.pid, {'version': w['version'], 'consent': True}))
        w = work.publish(self.c, self.uid, self.pid, {'version': w['version'], 'consent': True, 'confirmRoles': True})
        public = work.public(self.c, w['slug'])
        self.assertEqual(len(public['roles']), 4)
        self.assertEqual(public['roles'][0]['terms'], w['draft']['public']['conditions'])
        for private in ('roadmap', 'dependsOn', 'stageId', 'recruitmentReadiness', 'confirmedAt'): self.assertNotIn(private, str(public))

    def test_readiness_identifies_required_fields_and_accepts_explicitly_undecided_venue(self):
        w = self.prepared();d = w['draft'];t = next(t for t in d['tasks'] if t['recruit'])
        t['offer']['estimate'] = '';d['public']['conditions'] = ''
        w = self.edit(d)
        missing = w['recruitmentReadiness']['missing']
        self.assertTrue(any(v['field']=='estimate' and v['taskId']==t['id'] for v in missing))
        self.assertTrue(any(v['field']=='conditions' for v in missing))
        self.assertFalse(any(v['field']=='schedule' for v in missing))

    def test_applied_common_terms_stay_frozen_and_assignment_does_not_complete_work(self):
        w = self.publish();public = work.public(self.c, w['slug']);tid = public['roles'][0]['id']
        aid = work.apply(self.c, self.fan, self.pid, {'task': tid, 'experience': '協力します', 'consent': True, 'publicationRevision': public['publicationRevision']})['id']
        work.application_action(self.c, self.uid, aid, 'status', {'version': 1, 'status': '担当'})
        self.assertEqual(self.get()['roadmapProgress']['done'], 0)
        d = self.get()['draft'];d['public']['conditions'] = '変更した条件の非公開案';w = self.edit(d)
        self.problem(409, lambda: work.publish(self.c, self.uid, self.pid, {'version': w['version'], 'consent': True, 'confirmRoles': True}))
        self.assertEqual(work.public(self.c, w['slug']), public)

    def test_completion_requires_owner_all_tasks_confirmation_and_current_version(self):
        w = self.publish()
        self.problem(404, lambda: work.complete_goal(self.c, self.operator, self.pid, {'version': w['version'], 'confirm': True}))
        self.problem(409, lambda: work.complete_goal(self.c, self.uid, self.pid, {'version': w['version'], 'confirm': True}))
        d = w['draft']
        for t in d['tasks']: t['status'] = '完了'
        w = self.edit(d)
        self.problem(409, lambda: work.complete_goal(self.c, self.uid, self.pid, {'version': w['version']-1, 'confirm': True}))
        w = work.complete_goal(self.c, self.uid, self.pid, {'version': w['version'], 'confirm': True})
        self.assertIsNotNone(w['roadmapProgress']['confirmedAt'])
        self.assertEqual(w['state'], 'published')
        version = w['version']
        w = work.complete_goal(self.c, self.uid, self.pid, {'version': version, 'confirm': True})
        self.assertEqual(w['version'], version)
        d = w['draft'];d['overview'] += ' 内容説明だけを更新';w = self.edit(d)
        self.assertIsNotNone(w['roadmapProgress']['confirmedAt'])
        d = w['draft'];d['tasks'][0]['completion'] += ' 確認項目を追加';w = self.edit(d)
        self.assertIsNone(w['roadmapProgress']['confirmedAt'])

    def test_generic_edits_cannot_forge_completion_and_history_restore_requires_reconfirmation(self):
        w = self.prepared();d = w['draft'];d['roadmap']['confirmedAt'] = 12345
        w = work.operator_edit(self.c, self.operator, self.intake_id, {'version': w['version'], 'draft': d})
        self.assertIsNone(w['roadmapProgress']['confirmedAt'])
        for t in w['draft']['tasks']: t['status'] = '完了'
        w = self.edit(w['draft']);w = work.complete_goal(self.c, self.uid, self.pid, {'version': w['version'], 'confirm': True})
        confirmed = w['version']
        w = work.restore(self.c, self.uid, self.pid, {'version': confirmed, 'restoreVersion': confirmed})
        self.assertIsNone(w['roadmapProgress']['confirmedAt'])

    def test_initial_operator_draft_ids_and_roadmap_survive_delivery(self):
        item = intake.start(self.c, self.other, {'goal': '架空の写真展'})
        while item['state']=='interview':
            turn = next(t for t in item['turns'] if t['number']==item['step'])
            item = intake.answer(self.c, self.other, item['id'], {'step': item['step'], 'version': item['version'], 'questionHash': turn['questionHash'], 'answer': '未定', 'requestId': s.ident()})
        draft = initial();stage = s.ident()
        draft['roadmap'] = {'goal': '写真展を実施', 'criteria': '展示して片付ける', 'stages': [{'id': stage, 'title': '準備と実施', 'outcome': '写真展を終えられる'}]}
        for t in draft['tasks']: t.update(id=s.ident(), stageId=stage, dependsOn=[])
        saved = intake.save_result(self.c, self.operator, item['id'], {'draftVersion': 0, 'result': draft})
        ids = [t['id'] for t in saved['draft']['tasks']]
        old_payload = initial()
        self.problem(409, lambda: intake.save_result(self.c, self.operator, item['id'], {'draftVersion': saved['draftVersion'], 'result': old_payload}))
        self.assertEqual(intake.detail(self.c, self.operator, item['id'], True)['draft']['roadmap'], saved['draft']['roadmap'])
        before = intake.detail(self.c, self.other, item['id'])['turns']
        delivered = work.deliver(self.c, self.operator, item['id'], {'draftVersion': saved['draftVersion']})
        actual = work.detail(self.c, self.other, delivered['projectId'])
        self.assertEqual(ids, [t['id'] for t in actual['draft']['tasks']])
        self.assertTrue(actual['roadmapProgress']['ready'])
        self.assertEqual(before, intake.detail(self.c, self.other, item['id'])['turns'])

    def test_initial_delivery_requires_complete_roadmap_but_can_save_partial_draft(self):
        raw = initial();raw['roadmap'] = {'goal': '', 'criteria': '', 'stages': []}
        normalized = intake._result(raw)
        self.assertFalse(plan.progress(normalized)['ready'])
        # A saved draft can be incomplete; the existing delivered record is never overwritten.
        delivered = work.deliver(self.c, self.operator, self.intake_id, {'draftVersion': 0, 'result': raw})
        self.assertFalse(delivered['created'])
        self.assertIsNone(self.get()['draft']['roadmap'])

    def test_ai_adds_linked_task_with_temporary_id_and_keeps_existing_ids(self):
        w = self.prepared();d = copy.deepcopy(w['draft']);out = self.output()
        new = {k: '追加の'+k for k in work.TASK_FIELDS};out['newTasks'] = [new]
        out.update(roadmap=None, taskLinks=[{'id': 'new:0', 'stageId': d['roadmap']['stages'][-1]['id'], 'dependsOn': [d['tasks'][-1]['id']]}], publicOffers=[])
        actual = work._ai_edit(d, out)
        self.assertEqual([t['id'] for t in actual['tasks'][:14]], [t['id'] for t in w['draft']['tasks']])
        self.assertEqual(actual['tasks'][-1]['dependsOn'], [w['draft']['tasks'][-1]['id']])
        self.assertTrue(plan.progress(actual)['ready'])
        bad = self.output();bad['newTasks'] = [new]
        self.problem(502, lambda: work._ai_edit(copy.deepcopy(w['draft']), bad))

    def test_ai_roadmap_and_public_offer_are_private_validated_changes(self):
        w = self.prepared();d = copy.deepcopy(w['draft']);out = self.output();tid = d['tasks'][0]['id']
        out.update(roadmap={'goal': '変更した完成形', 'criteria': '確認項目', 'stages': d['roadmap']['stages']},taskLinks=[],
                   publicOffers=[{'taskId': tid, **{k: '外向けの'+k for k in work.TASK_FIELDS}, 'terms': '条件を相談', 'useCommonTerms': False}])
        actual = work._ai_edit(d, out)
        self.assertEqual(actual['roadmap']['goal'], '変更した完成形')
        self.assertEqual(actual['tasks'][0]['offer']['title'], '外向けのtitle')
        self.assertFalse(actual['tasks'][0]['recruit'])
        self.assertEqual(self.get()['published'], {})

    def test_completion_route_is_owner_only_and_csrf_protected(self):
        w = self.prepared();path='/api/service/projects/'+self.pid+'/complete-goal'
        self.assertEqual(self.request('POST', path, {'version': w['version'], 'confirm': True}, csrf='wrong')['status'], 403)
        self.assertEqual(self.request('POST', path, {'version': w['version'], 'confirm': True}, uid=self.other)['status'], 404)
        self.problem(403, lambda: work.prepare_planning_managed(self.c, self.intake_id, {}, 'someone@example.test'))


if __name__=='__main__': unittest.main()
