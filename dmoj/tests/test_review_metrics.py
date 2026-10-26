import hashlib
import math
import unittest

from dmoj.review_evidence import digest, run_review_job


class NativeMetricProbeTests(unittest.TestCase):
    def run_metric(self, name, answer, candidate, options=None):
        payload = {'source': {'source': '', 'language': 'BUILTIN', 'sha256': hashlib.sha256(b'').hexdigest()},
                   'builtin_checker': name, 'checker_args': options or {},
                   'cases': [{'id': 'toy', 'input': '', 'expected_output': answer, 'candidate_output': candidate}]}
        result = run_review_job({'operation': 'checker_probe', 'snapshot_hash': 'snapshot', 'payload_hash': digest(payload), 'payload': payload})
        self.assertEqual(result['failed'], 0, result)
        return result['cases'][0]['checker_result']

    def test_all_six_metrics_match_hand_calculations(self):
        examples = (
            ('csv_accuracy', 'id,y\na,1\nb,0\nc,1\nd,0\n', 'id,y\na,1\nb,0\nc,0\nd,1\n', .5),
            ('csv_f1', 'id,y\na,0\nb,0\nc,1\nd,1\n', 'id,y\na,0\nb,0\nc,0\nd,0\n', 1/3),
            ('csv_auc', 'id,y\na,0\nb,0\nc,1\nd,1\n', 'id,y\na,0.1\nb,0.4\nc,0.35\nd,0.8\n', .75),
            ('csv_rmse', 'id,y\na,1\nb,2\n', 'id,y\na,2\nb,2\n', 1/(1+math.sqrt(.5))),
            ('csv_mae', 'id,y\na,1\nb,2\n', 'id,y\na,2\nb,2\n', 2/3),
            ('csv_logloss', 'id,y\na,0\nb,1\n', 'id,y\na,0.25\nb,0.75\n', 1/(1-math.log(.75))),
        )
        for name, answer, candidate, score in examples:
            with self.subTest(name=name):
                observed = self.run_metric(name, answer, candidate)
                self.assertAlmostEqual(observed['points'], score)
                self.assertFalse(observed['passed'])  # A valid partial score is not full AC.

    def test_tolerance_options_reach_the_installed_checker(self):
        for candidate, passed in (('1.0000005', True), ('1.000002', False), ('nan', False)):
            with self.subTest(candidate=candidate):
                self.assertEqual(self.run_metric('floatsabs', '1', candidate, {'precision': 6})['passed'], passed)

    def test_baseline_and_id_alignment(self):
        observed = self.run_metric('csv_mae', 'id,y\na,1\nb,2\n', 'id,y\nb,2\na,2\n', {'baseline': 2})
        self.assertAlmostEqual(observed['points'], .75)

    def test_public_fraction_is_separate_from_full_population(self):
        ids = [str(i) for i in range(20)]
        answer = 'id,y\n' + ''.join('%s,1\n' % key for key in ids)
        candidate = 'id,y\n' + ''.join('%s,%s\n' % (key, int(int(key) < 10)) for key in ids)
        selected = [key for key in ids if int.from_bytes(hashlib.md5(key.encode()).digest()[:4], 'big') % 1000 < 500]
        full = self.run_metric('csv_accuracy', answer, candidate, {'pretest_fraction': .5, 'pretests_only': False})
        public = self.run_metric('csv_accuracy', answer, candidate, {'pretest_fraction': .5, 'pretests_only': True})
        self.assertEqual(full['points'], .5)
        self.assertAlmostEqual(public['points'], sum(int(key) < 10 for key in selected) / len(selected))


if __name__ == '__main__':
    unittest.main()
