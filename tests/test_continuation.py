from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import continuation


class ContinuationTests(unittest.TestCase):
    def test_choose_uses_available_profile_or_earliest_verified_reset_with_margin(self):
        self.assertEqual(continuation.choose('codex',{'codex':{'usedPercent':100,'resetsAt':400},'codex-2':{'usedPercent':89,'resetsAt':500}},now=100),('codex-2',None))
        self.assertEqual(continuation.choose('codex',{'codex':{'usedPercent':100,'resetsAt':400},'codex-2':{'usedPercent':90,'resetsAt':300}},now=100),('codex-2',360))
        with self.assertRaises(ValueError):continuation.choose('codex',{'codex':{'usedPercent':100,'resetsAt':90}},now=100)

    def test_exhausted_profiles_schedule_without_starting_any_model(self):
        windows={'codex':{'usedPercent':100,'resetsAt':400},'codex-2':{'usedPercent':90,'resetsAt':300}}
        with patch.object(continuation,'read_state',return_value={'runId':'fixture','status':'scheduled'}), \
             patch.object(continuation,'quota',side_effect=lambda name:windows[name]), \
             patch.object(continuation.time,'time',return_value=100), \
             patch.object(continuation,'schedule',return_value={'status':'scheduled'}) as schedule, \
             patch.object(continuation.subprocess,'Popen') as model:
            continuation.run('codex','fixture');schedule.assert_called_once_with('codex-2',360);model.assert_not_called()

    def test_weekly_exhaustion_and_cancelled_jobs_cannot_start_a_model(self):
        import session_usage
        response={'rateLimits':{'primary':{'usedPercent':0,'windowDurationMins':300,'resetsAt':200},'secondary':{'usedPercent':100,'windowDurationMins':10080,'resetsAt':500}}}
        window=session_usage.five_hour_window(response)
        available=session_usage.continuation_availability(response,window)
        self.assertFalse(available['availableForContinuation'])
        self.assertEqual(available['continuationResetsAt'],500)
        with patch.object(continuation,'read_state',return_value={'runId':'fixture','status':'cancelled'}),patch.object(continuation,'quota') as quota:
            result=continuation.run('codex','fixture');quota.assert_not_called()
        self.assertFalse(result['modelStarted'])

    def test_dry_run_never_writes_state_or_executes_a_timer(self):
        with patch.object(continuation,'write_state') as state,patch.object(continuation.subprocess,'run') as command:
            result=continuation.schedule('codex',9999999999,dry_run=True)
            state.assert_not_called();command.assert_not_called()
        self.assertIn('--timer-property=Persistent=true',result['command'])
        self.assertIn('--expand-environment=no',result['command'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',result['command'])


if __name__=='__main__':unittest.main()
