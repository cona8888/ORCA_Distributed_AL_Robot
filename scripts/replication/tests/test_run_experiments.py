import unittest

from scripts.replication.run_experiments import (
    aggregate_results,
    build_plan,
    load_contract,
    parse_simulator_output,
    render_config,
    wilson_interval_95,
)


class ReplicationRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract()

    def test_full_plan_has_every_paper_trial(self):
        self.assertEqual(len(build_plan(self.contract, "all", 50)), 7500)

    def test_quick_plan_covers_each_quantitative_experiment(self):
        plan = build_plan(self.contract, "quick", 1)
        self.assertEqual(
            {spec.experiment for spec in plan},
            {
                "continuous_convergence",
                "sliding_window",
                "discrete_convergence",
                "sigma_sweep",
                "informed_robots",
            },
        )

    def test_continuous_template_uses_paper_message_schedule(self):
        spec = build_plan(self.contract, "continuous", 1)[0]
        rendered = render_config(spec)
        self.assertIn("NUM_ITERS: 1", rendered)
        self.assertIn("SLIDING_WINDOW_TIMESTEP_FREQ: 1", rendered)
        self.assertIn("TURNING_RADIUS: 2.0", rendered)
        self.assertIn("SIGMA_FACTOR_SMOOTHNESS: 0.1", rendered)
        self.assertIn("SIGMA_VAR_SLIDING_WINDOW: 0.01", rendered)

    def test_informed_template_uses_reported_fixed_prior(self):
        spec = build_plan(self.contract, "informed", 1)[0]
        rendered = render_config(spec)
        self.assertIn("SIGMA_POSE_FIXED: 1e-10", rendered)
        self.assertIn("SEED_DECISION: 5", rendered)
        self.assertIn("NUM_ITERS: 1", rendered)

    def test_output_parser(self):
        output = """
Consensus reached. Final decision: [2]
Final iterations: [17]
Final robot count: [500]
Mean connectivity: [4.250000 neighbours/robot]
Mean formation parameter error_heading: [0.009000 rad].
Mean formation parameter error_pos: [0.050000 m]
"""
        metrics = parse_simulator_output(output)
        self.assertTrue(metrics["converged"])
        self.assertEqual(metrics["iterations"], 17)
        self.assertEqual(metrics["robot_count"], 500)
        self.assertEqual(metrics["final_decision"], 2)
        self.assertAlmostEqual(metrics["mean_connectivity"], 4.25)
        self.assertAlmostEqual(metrics["final_error_heading_rad"], 0.009)
        self.assertAlmostEqual(metrics["final_error_position_m"], 0.05)

    def test_no_consensus_is_not_parsed_as_success(self):
        metrics = parse_simulator_output(
            "MAX TIME ELASPED! No Consensus reached.\nFinal iterations: [1000]\n"
        )
        self.assertFalse(metrics["converged"])
        self.assertTrue(metrics["max_iterations_elapsed"])

    def test_table_one_seed_result_is_statistically_compatible(self):
        lower, upper = wilson_interval_95(43, 50)
        self.assertLessEqual(lower, 0.8)
        self.assertGreaterEqual(upper, 0.8)

    def test_censored_iterations_are_not_treated_as_convergence(self):
        rows = [
            {
                "experiment": "sliding_window",
                "parameters": {"window_timesteps": 0},
                "error": None,
                "metrics": {
                    "converged": True,
                    "iterations": 100,
                    "max_iterations_elapsed": False,
                },
            },
            {
                "experiment": "sliding_window",
                "parameters": {"window_timesteps": 0},
                "error": None,
                "metrics": {
                    "converged": False,
                    "iterations": 5000,
                    "max_iterations_elapsed": True,
                },
            },
        ]
        aggregate = aggregate_results(rows)[0]
        self.assertEqual(aggregate["converged_runs"], 1)
        self.assertEqual(aggregate["censored_runs"], 1)
        self.assertEqual(aggregate["mean_iterations_converged"], 100)
        self.assertEqual(
            aggregate["mean_iterations_all_valid_including_censoring"], 2550
        )


if __name__ == "__main__":
    unittest.main()
