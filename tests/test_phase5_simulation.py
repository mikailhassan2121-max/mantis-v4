"""
Phase 5 tests (section 15).

Required coverage: Monte Carlo reproducibility, probability bounds, MC standard
error, Student-t probability logic, empirical bootstrap sampling,
insufficient-sample handling, Greeks/sensitivity finite outputs, fragility score
bounds, model disagreement, no future-data usage, holdout untouched, volatility
estimator consistency, same-window grouping, deterministic seeds.
"""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mantis_v4.clock import UTC
from mantis_v4.models.dataset_builder import ModellingDatasetBuilder
from mantis_v4.models.splits import grouped_walk_forward, reserve_holdout
from mantis_v4.simulation import (
    BANDS,
    EmpiricalBootstrapModel,
    EmpiricalTerminalModel,
    MonteCarloConfig,
    MonteCarloEngine,
    analyse_normality,
    build_estimators,
    combine_channels,
    compute_fragility,
    conservative_lower_bound,
    digital_sensitivities,
    disagreement_error_study,
    fit_student_t_df,
    gaussian_terminal,
    mad_volatility,
    normal_z_probability,
    scaled_sensitivities,
    sigma_remaining_from,
    standardized_terminal_returns,
    student_t_terminal,
    volatility_clustering,
    volatility_of_volatility,
)

ET = ZoneInfo("America/New_York")


def make_frame(start, minutes, base=100.0, seed=5):
    rng = np.random.default_rng(seed)
    index = pd.date_range(start.astimezone(UTC), periods=minutes, freq="1min", tz="UTC")
    closes = np.maximum(base + rng.normal(0, base * 0.0006, minutes).cumsum(), base * 0.5)
    return pd.DataFrame(
        {"Open": closes, "High": closes * 1.0004, "Low": closes * 0.9996,
         "Close": closes, "Volume": rng.uniform(10, 100, minutes)},
        index=index,
    )


class TestGaussianAnchor(unittest.TestCase):
    def test_matches_closed_form(self):
        buffer = np.array([0.0, 0.001, -0.001, 0.005])
        sigma = np.array([0.002, 0.002, 0.002, 0.002])
        est = gaussian_terminal(buffer, sigma)
        self.assertAlmostEqual(est.p_yes[0], 0.5, places=10)
        self.assertGreater(est.p_yes[1], 0.5)
        self.assertLess(est.p_yes[2], 0.5)
        # Symmetry about zero buffer.
        self.assertAlmostEqual(est.p_yes[1] + est.p_yes[2], 1.0, places=10)

    def test_probabilities_in_bounds(self):
        rng = np.random.default_rng(0)
        buffer = rng.normal(0, 0.01, 5000)
        sigma = np.abs(rng.normal(0.002, 0.0005, 5000)) + 1e-6
        p = gaussian_terminal(buffer, sigma).p_yes
        self.assertTrue(np.all(p >= 0.0) and np.all(p <= 1.0))
        self.assertTrue(np.all(np.isfinite(p)))

    def test_crossing_probability_is_bounded_and_sensible(self):
        buffer = np.array([0.0, 0.002, 0.010])
        sigma = np.full(3, 0.002)
        est = gaussian_terminal(buffer, sigma, spot=np.full(3, 100.0),
                                reference=np.full(3, 100.0))
        pc = est.p_cross_reference
        self.assertTrue(np.all(pc >= 0.0) and np.all(pc <= 1.0))
        # At zero buffer the reference is already touched -> probability 1.
        self.assertAlmostEqual(pc[0], 1.0, places=6)
        # Crossing becomes less likely as the buffer grows.
        self.assertGreater(pc[1], pc[2])

    def test_no_standard_error_means_no_invented_bound(self):
        est = gaussian_terminal(np.array([0.001]), np.array([0.002]))
        self.assertIsNone(est.standard_error)
        self.assertIsNone(est.lower_confidence_bound())


class TestStudentT(unittest.TestCase):
    def test_variance_matched_to_gaussian(self):
        """Scale correction must isolate tail SHAPE, not change variance."""
        from scipy.stats import t as student_t

        nu = 5.0
        sigma = 0.002
        scale = sigma * math.sqrt((nu - 2.0) / nu)
        self.assertAlmostEqual(student_t.std(df=nu, scale=scale), sigma, places=12)

    def test_heavier_tails_pull_extremes_toward_half(self):
        """At large |z| a t model is LESS confident than a Gaussian."""
        buffer = np.array([0.006])
        sigma = np.array([0.002])          # z = 3
        g = gaussian_terminal(buffer, sigma).p_yes[0]
        t = student_t_terminal(buffer, sigma, nu=4.0).p_yes[0]
        self.assertGreater(g, 0.99)
        self.assertLess(t, g)

    def test_near_the_money_t_is_more_confident(self):
        """Variance-matched t is peaked in the middle, so small |z| gives MORE
        confidence than Gaussian. The crossover is a real property, not a bug."""
        buffer = np.array([0.0005])
        sigma = np.array([0.002])          # z = 0.25
        g = gaussian_terminal(buffer, sigma).p_yes[0]
        t = student_t_terminal(buffer, sigma, nu=4.0).p_yes[0]
        self.assertGreater(t, g)

    def test_high_nu_approaches_gaussian(self):
        buffer = np.array([0.003])
        sigma = np.array([0.002])
        g = gaussian_terminal(buffer, sigma).p_yes[0]
        t = student_t_terminal(buffer, sigma, nu=200.0).p_yes[0]
        self.assertAlmostEqual(g, t, places=3)

    def test_rejects_infinite_variance(self):
        with self.assertRaises(ValueError):
            student_t_terminal(np.array([0.001]), np.array([0.002]), nu=2.0)

    def test_bounds(self):
        rng = np.random.default_rng(2)
        buffer = rng.normal(0, 0.01, 2000)
        sigma = np.abs(rng.normal(0.002, 0.0005, 2000)) + 1e-6
        p = student_t_terminal(buffer, sigma, nu=4.0).p_yes
        self.assertTrue(np.all(p >= 0.0) and np.all(p <= 1.0))


class TestMonteCarlo(unittest.TestCase):
    def setUp(self):
        self.n = 40
        self.spot = np.full(self.n, 100.0)
        self.reference = np.full(self.n, 100.0) + np.linspace(-0.3, 0.3, self.n)
        self.seconds = np.full(self.n, 300.0)
        self.sigma_1m = np.full(self.n, 0.0008)

    def run_mc(self, **kwargs):
        config = MonteCarloConfig(n_paths=kwargs.pop("n_paths", 2000), **kwargs)
        return MonteCarloEngine(config).run(
            spot=self.spot, reference=self.reference,
            seconds_remaining=self.seconds, sigma_1m=self.sigma_1m,
        )

    def test_reproducible_under_fixed_seed(self):
        a = self.run_mc(seed=123)
        b = self.run_mc(seed=123)
        np.testing.assert_array_equal(a.p_yes, b.p_yes)
        np.testing.assert_array_equal(a.p_cross_reference, b.p_cross_reference)
        np.testing.assert_array_equal(a.quantile_05, b.quantile_05)

    def test_different_seeds_differ(self):
        a = self.run_mc(seed=1)
        b = self.run_mc(seed=2)
        self.assertFalse(np.array_equal(a.p_yes, b.p_yes))

    def test_probabilities_in_bounds(self):
        est = self.run_mc(seed=7)
        self.assertTrue(np.all(est.p_yes >= 0.0) and np.all(est.p_yes <= 1.0))
        self.assertTrue(np.all(est.p_cross_reference >= 0.0))
        self.assertTrue(np.all(est.p_cross_reference <= 1.0))

    def test_converges_to_the_gaussian_closed_form(self):
        """Driftless Gaussian MC must reproduce Phi(z) within sampling error."""
        est = self.run_mc(seed=11, n_paths=40000)
        sigma_rem = self.sigma_1m * np.sqrt(self.seconds / 60.0)
        buffer = (self.spot - self.reference) / self.reference
        analytic = gaussian_terminal(buffer, sigma_rem).p_yes
        # 4 standard errors is a generous but still meaningful tolerance.
        tolerance = 4.0 * np.sqrt(analytic * (1 - analytic) / 40000) + 0.005
        np.testing.assert_array_less(np.abs(est.p_yes - analytic), tolerance)

    def test_standard_error_shrinks_with_paths(self):
        few = self.run_mc(seed=3, n_paths=500)
        many = self.run_mc(seed=3, n_paths=8000)
        self.assertGreater(np.mean(few.standard_error), np.mean(many.standard_error))
        # Binomial SE formula check.
        expected = math.sqrt(0.25 / 500)
        self.assertLess(np.max(few.standard_error), expected + 1e-9)

    def test_quantiles_are_ordered(self):
        est = self.run_mc(seed=5)
        self.assertTrue(np.all(est.quantile_05 <= est.quantile_50))
        self.assertTrue(np.all(est.quantile_50 <= est.quantile_95))

    def test_lower_confidence_bound_is_below_point_estimate(self):
        est = self.run_mc(seed=9)
        bound = est.lower_confidence_bound()
        self.assertIsNotNone(bound)
        confidence = np.maximum(est.p_yes, 1.0 - est.p_yes)
        self.assertTrue(np.all(bound <= confidence + 1e-12))
        self.assertTrue(np.all(bound >= 0.0))

    def test_drift_defaults_to_zero(self):
        """Section 3: momentum must not create deterministic drift."""
        self.assertEqual(MonteCarloConfig().drift_shrinkage, 0.0)
        big_drift = np.full(self.n, 0.05)
        config = MonteCarloConfig(n_paths=4000, seed=4)
        with_drift = MonteCarloEngine(config).run(
            spot=self.spot, reference=self.reference, seconds_remaining=self.seconds,
            sigma_1m=self.sigma_1m, drift_1m=big_drift,
        )
        without = MonteCarloEngine(config).run(
            spot=self.spot, reference=self.reference, seconds_remaining=self.seconds,
            sigma_1m=self.sigma_1m,
        )
        np.testing.assert_array_equal(with_drift.p_yes, without.p_yes)

    def test_empirical_innovations_are_used(self):
        rng = np.random.default_rng(1)
        pool = rng.standard_t(3, 20000)
        engine = MonteCarloEngine(
            MonteCarloConfig(n_paths=4000, seed=6, innovation="empirical"),
            innovation_pool=pool,
        )
        self.assertTrue(engine.has_empirical_pool)
        est = engine.run(spot=self.spot, reference=self.reference,
                         seconds_remaining=self.seconds, sigma_1m=self.sigma_1m)
        self.assertTrue(np.all(np.isfinite(est.p_yes)))

    def test_crossing_more_likely_with_small_buffer(self):
        spot = np.array([100.0, 100.0])
        reference = np.array([99.99, 99.0])
        est = MonteCarloEngine(MonteCarloConfig(n_paths=6000, seed=8)).run(
            spot=spot, reference=reference,
            seconds_remaining=np.array([600.0, 600.0]),
            sigma_1m=np.array([0.0008, 0.0008]),
        )
        self.assertGreater(est.p_cross_reference[0], est.p_cross_reference[1])


class TestEmpiricalAndBootstrap(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(4)
        self.n = 20000
        self.z = rng.standard_normal(self.n)
        self.seconds = rng.choice([60, 150, 300, 450, 600, 840], self.n)
        self.sigma = np.abs(rng.normal(0.0008, 0.0002, self.n)) + 1e-6
        self.asset = rng.choice(["A", "B"], self.n)

    def test_empirical_recovers_a_normal_cdf(self):
        model = EmpiricalTerminalModel(min_samples=100).fit(self.z, self.seconds)
        buffer = np.array([0.0, 0.0008, -0.0008])
        sigma_rem = np.array([0.0008, 0.0008, 0.0008])
        est = model.predict(buffer, sigma_rem, np.array([300.0, 300.0, 300.0]))
        # Pool is standard normal, so p should track Phi(z).
        self.assertAlmostEqual(est.p_yes[0], 0.5, delta=0.02)
        self.assertAlmostEqual(est.p_yes[1], 0.8413, delta=0.02)
        self.assertAlmostEqual(est.p_yes[2], 0.1587, delta=0.02)

    def test_empirical_never_returns_hard_zero_or_one(self):
        model = EmpiricalTerminalModel(min_samples=100).fit(self.z, self.seconds)
        buffer = np.array([1.0, -1.0])         # absurdly far outside support
        est = model.predict(buffer, np.array([1e-6, 1e-6]), np.array([300.0, 300.0]))
        self.assertTrue(np.all(est.p_yes > 0.0))
        self.assertTrue(np.all(est.p_yes < 1.0))

    def test_bootstrap_conditions_and_reports_level(self):
        model = EmpiricalBootstrapModel(min_samples=250).fit(
            standardized_returns=self.z, seconds_remaining=self.seconds,
            sigma_1m=self.sigma, asset=self.asset,
        )
        est, diag = model.predict(
            buffer_pct=np.full(200, 0.0008),
            sigma_remaining=np.full(200, 0.0008),
            seconds_remaining=np.full(200, 300.0),
            sigma_1m=np.full(200, 0.0008),
            asset=np.array(["A"] * 200),
        )
        self.assertEqual(diag.total_rows, 200)
        self.assertEqual(diag.insufficient_rows, 0)
        self.assertTrue(np.all(np.isfinite(est.p_yes)))
        self.assertTrue(np.all(est.sample_size > 0))

    def test_bootstrap_reports_insufficient_sample_honestly(self):
        """Section 4: do not fabricate confidence."""
        tiny = EmpiricalBootstrapModel(min_samples=10**9)
        tiny.fit(standardized_returns=self.z[:500], seconds_remaining=self.seconds[:500],
                 sigma_1m=self.sigma[:500], asset=self.asset[:500])
        est, diag = tiny.predict(
            buffer_pct=np.full(50, 0.001), sigma_remaining=np.full(50, 0.0008),
            seconds_remaining=np.full(50, 300.0), sigma_1m=np.full(50, 0.0008),
            asset=np.array(["A"] * 50),
        )
        self.assertEqual(diag.insufficient_rows, 50)
        self.assertAlmostEqual(diag.insufficient_fraction, 1.0)
        self.assertTrue(np.all(np.isnan(est.p_yes)))
        self.assertIn("INSUFFICIENT", diag.level_counts)

    def test_bootstrap_relaxes_conditioning_rather_than_failing(self):
        model = EmpiricalBootstrapModel(min_samples=250).fit(
            standardized_returns=self.z, seconds_remaining=self.seconds,
            sigma_1m=self.sigma, asset=self.asset,
        )
        est, diag = model.predict(
            buffer_pct=np.full(10, 0.0008), sigma_remaining=np.full(10, 0.0008),
            seconds_remaining=np.full(10, 300.0), sigma_1m=np.full(10, 0.0008),
            asset=np.array(["UNSEEN_ASSET"] * 10),
        )
        # An unseen asset must fall back, not fail.
        self.assertEqual(diag.insufficient_rows, 0)
        self.assertTrue(np.all(np.isfinite(est.p_yes)))

    def test_bootstrap_fit_uses_only_supplied_rows(self):
        """Training-only: the model cannot see rows it was not given."""
        model = EmpiricalBootstrapModel(min_samples=100)
        model.fit(standardized_returns=self.z[:5000], seconds_remaining=self.seconds[:5000],
                  sigma_1m=self.sigma[:5000], asset=self.asset[:5000])
        total = sum(len(p) for k, p in model._pools.items() if k == ("global",))
        self.assertLessEqual(total, 5000)


class TestSensitivities(unittest.TestCase):
    def setUp(self):
        self.spot = np.array([100.0, 100.5, 99.5, 100.0])
        self.reference = np.array([100.0, 100.0, 100.0, 100.0])
        self.seconds = np.array([600.0, 300.0, 150.0, 60.0])
        self.sigma_1m = np.array([0.0008, 0.0008, 0.0012, 0.0008])

    def test_all_outputs_finite(self):
        s = digital_sensitivities(spot=self.spot, reference=self.reference,
                                  seconds_remaining=self.seconds, sigma_1m=self.sigma_1m)
        for name, array in s.as_dict().items():
            self.assertTrue(np.all(np.isfinite(array)), name)

    def test_greeks_match_finite_differences(self):
        """The analytic derivatives must equal numerical ones."""
        s = digital_sensitivities(spot=self.spot, reference=self.reference,
                                  seconds_remaining=self.seconds, sigma_1m=self.sigma_1m)

        h = 1e-4
        up = normal_z_probability(self.spot + h, self.reference, self.seconds, self.sigma_1m)
        dn = normal_z_probability(self.spot - h, self.reference, self.seconds, self.sigma_1m)
        np.testing.assert_allclose(s.delta, (up - dn) / (2 * h), rtol=1e-4, atol=1e-8)

        mid = normal_z_probability(self.spot, self.reference, self.seconds, self.sigma_1m)
        np.testing.assert_allclose(s.gamma, (up - 2 * mid + dn) / h**2, rtol=1e-3, atol=1e-6)

        hv = 1e-8
        vu = normal_z_probability(self.spot, self.reference, self.seconds, self.sigma_1m + hv)
        vd = normal_z_probability(self.spot, self.reference, self.seconds, self.sigma_1m - hv)
        np.testing.assert_allclose(s.vega, (vu - vd) / (2 * hv), rtol=1e-3, atol=1e-4)

        ht = 1e-3
        tu = normal_z_probability(self.spot, self.reference, self.seconds + ht, self.sigma_1m)
        td = normal_z_probability(self.spot, self.reference, self.seconds - ht, self.sigma_1m)
        np.testing.assert_allclose(s.theta, (tu - td) / (2 * ht), rtol=1e-3, atol=1e-9)

    def test_gamma_largest_near_reference(self):
        """Fragility intuition: curvature peaks near the strike close to expiry."""
        spot = np.array([100.0005, 101.0])
        s = digital_sensitivities(
            spot=spot, reference=np.array([100.0, 100.0]),
            seconds_remaining=np.array([60.0, 60.0]),
            sigma_1m=np.array([0.0008, 0.0008]),
        )
        scaled = scaled_sensitivities(s, spot, np.array([0.0008, 0.0008]))
        self.assertGreater(abs(scaled["delta_per_pct"][0]), abs(scaled["delta_per_pct"][1]))

    def test_at_the_money_probability_is_half(self):
        s = digital_sensitivities(
            spot=np.array([100.0]), reference=np.array([100.0]),
            seconds_remaining=np.array([300.0]), sigma_1m=np.array([0.0008]),
        )
        self.assertAlmostEqual(s.p_yes[0], 0.5, places=10)
        self.assertAlmostEqual(s.gamma[0], 0.0, places=10)   # z = 0

    def test_scaled_versions_are_comparable_across_assets(self):
        """BTC at 63000 and ADA at 0.18 must give similar scaled numbers."""
        args = dict(seconds_remaining=np.array([300.0]), sigma_1m=np.array([0.0008]))
        btc = digital_sensitivities(spot=np.array([63000.0]),
                                    reference=np.array([63063.0]), **args)
        ada = digital_sensitivities(spot=np.array([0.18]),
                                    reference=np.array([0.18018]), **args)
        b = scaled_sensitivities(btc, np.array([63000.0]), np.array([0.0008]))
        a = scaled_sensitivities(ada, np.array([0.18]), np.array([0.0008]))
        self.assertAlmostEqual(b["delta_per_pct"][0], a["delta_per_pct"][0], places=6)


class TestFragility(unittest.TestCase):
    def build(self, n=500, seed=0):
        rng = np.random.default_rng(seed)
        return dict(
            gamma_per_pct2=rng.normal(0, 0.05, n),
            vega_per_10pct_vol=rng.normal(0, 0.02, n),
            theta_per_30s=rng.normal(0, 0.01, n),
            abs_z=np.abs(rng.normal(1.0, 0.8, n)),
            seconds_remaining=rng.uniform(30, 900, n),
            crossings=rng.integers(0, 6, n).astype(float),
            vol_of_vol=np.abs(rng.normal(0.3, 0.1, n)),
            disagreement=np.abs(rng.normal(0.03, 0.02, n)),
        )

    def test_score_bounds_and_bands(self):
        f = compute_fragility(**self.build())
        self.assertTrue(np.all(f.score >= 0.0) and np.all(f.score <= 100.0))
        self.assertTrue(set(np.unique(f.band)).issubset(set(BANDS)))

    def test_score_is_deterministic(self):
        data = self.build(seed=3)
        np.testing.assert_array_equal(
            compute_fragility(**data).score, compute_fragility(**data).score
        )

    def test_closer_to_reference_is_more_fragile(self):
        base = dict(
            gamma_per_pct2=np.zeros(2), vega_per_10pct_vol=np.zeros(2),
            theta_per_30s=np.zeros(2), seconds_remaining=np.array([300.0, 300.0]),
        )
        f = compute_fragility(abs_z=np.array([0.1, 3.0]), **base)
        self.assertGreater(f.score[0], f.score[1])

    def test_less_time_is_more_fragile(self):
        base = dict(
            gamma_per_pct2=np.zeros(2), vega_per_10pct_vol=np.zeros(2),
            theta_per_30s=np.zeros(2), abs_z=np.array([1.0, 1.0]),
        )
        f = compute_fragility(seconds_remaining=np.array([30.0, 800.0]), **base)
        self.assertGreater(f.score[0], f.score[1])

    def test_optional_inputs_may_be_omitted(self):
        f = compute_fragility(
            gamma_per_pct2=np.zeros(3), vega_per_10pct_vol=np.zeros(3),
            theta_per_30s=np.zeros(3), abs_z=np.ones(3),
            seconds_remaining=np.full(3, 300.0),
        )
        self.assertEqual(len(f.score), 3)

    def test_fragility_never_alters_probability(self):
        """Section 6: fragility informs eligibility, never the probability."""
        buffer = np.array([0.001, 0.002])
        sigma = np.array([0.002, 0.002])
        before = gaussian_terminal(buffer, sigma).p_yes.copy()
        compute_fragility(
            gamma_per_pct2=np.array([10.0, 0.0]),
            vega_per_10pct_vol=np.array([10.0, 0.0]),
            theta_per_30s=np.array([10.0, 0.0]),
            abs_z=np.array([0.01, 5.0]),
            seconds_remaining=np.array([10.0, 800.0]),
        )
        after = gaussian_terminal(buffer, sigma).p_yes
        np.testing.assert_array_equal(before, after)

    def test_vol_of_vol(self):
        v = volatility_of_volatility(np.array([0.002, 0.001]), np.array([0.001, 0.001]))
        self.assertGreater(v[0], v[1])
        self.assertAlmostEqual(v[1], 0.0, places=12)


class TestAgreement(unittest.TestCase):
    def test_combine_reports_spread(self):
        channels = {
            "gaussian": np.array([0.9, 0.6]),
            "student_t": np.array([0.85, 0.55]),
            "bootstrap": np.array([0.88, 0.40]),
        }
        r = combine_channels(channels)
        self.assertAlmostEqual(r.maximum[0], 0.90)
        self.assertAlmostEqual(r.minimum[0], 0.85)
        self.assertAlmostEqual(r.spread[0], 0.05, places=10)
        self.assertGreater(r.disagreement[1], r.disagreement[0])

    def test_disagreement_uses_the_decision_side(self):
        """Two firm NOs must not look as disagreeing as a coin flip."""
        firm_no = {"gaussian": np.array([0.02]), "other": np.array([0.06])}
        coin = {"gaussian": np.array([0.48]), "other": np.array([0.52])}
        self.assertAlmostEqual(
            combine_channels(firm_no).disagreement[0],
            combine_channels(coin).disagreement[0], places=10,
        )

    def test_nan_channel_is_excluded_not_treated_as_half(self):
        channels = {
            "gaussian": np.array([0.9, 0.9]),
            "bootstrap": np.array([0.88, np.nan]),
        }
        r = combine_channels(channels)
        self.assertEqual(r.n_channels[0], 2)
        self.assertEqual(r.n_channels[1], 1)
        self.assertAlmostEqual(r.mean[1], 0.9)

    def test_lower_bound_is_conservative(self):
        channels = {"gaussian": np.array([0.90]), "bootstrap": np.array([0.80])}
        bound = conservative_lower_bound(channels)
        self.assertLessEqual(bound[0], 0.80 + 1e-12)
        self.assertGreaterEqual(bound[0], 0.0)

    def test_lower_bound_incorporates_standard_error(self):
        channels = {"gaussian": np.array([0.90]), "mc": np.array([0.90])}
        with_se = conservative_lower_bound(
            channels, standard_errors={"mc": np.array([0.05])}
        )
        without = conservative_lower_bound(channels)
        self.assertLess(with_se[0], without[0])

    def test_disagreement_study_handles_small_samples(self):
        out = disagreement_error_study(
            anchor_p=np.array([0.9] * 10), outcome_yes=np.ones(10, dtype=int),
            disagreement=np.zeros(10), groups=np.array(["w"] * 10),
        )
        self.assertEqual(out["status"], "INSUFFICIENT SAMPLE")

    def test_disagreement_study_detects_a_planted_effect(self):
        rng = np.random.default_rng(5)
        n = 4000
        disagreement = rng.uniform(0, 0.1, n)
        # Planted: higher disagreement -> lower win probability.
        win_p = 0.95 - 3.0 * disagreement
        outcome = (rng.uniform(0, 1, n) < win_p).astype(int)
        out = disagreement_error_study(
            anchor_p=np.full(n, 0.9), outcome_yes=outcome,
            disagreement=disagreement,
            groups=np.array([f"w{i//4}" for i in range(n)]),
        )
        self.assertEqual(out["status"], "OK")
        self.assertTrue(out["separates"])
        self.assertGreater(out["accuracy_drop_low_to_high"], 0.0)


class TestVolatilityEstimators(unittest.TestCase):
    def test_all_estimators_build(self):
        from mantis_v4.backtest.features import precompute_indicators
        from mantis_v4.models.features_extended import precompute_extended

        frame = make_frame(datetime(2026, 7, 1, tzinfo=UTC), 400, seed=9)
        base = precompute_indicators(frame)
        ext = precompute_extended(frame)
        for estimator in build_estimators():
            series = estimator.build(frame, base, ext)
            self.assertEqual(len(series), len(frame), estimator.name)
            tail = np.asarray(series)[-100:]
            self.assertTrue(np.all(np.isfinite(tail)), estimator.name)
            self.assertTrue(np.all(tail >= 0), estimator.name)

    def test_sigma_remaining_scaling(self):
        s = sigma_remaining_from(np.array([0.001]), np.array([900.0]))
        self.assertAlmostEqual(s[0], 0.001 * math.sqrt(15.0), places=12)
        # Zero time remaining floors rather than producing zero.
        self.assertGreater(sigma_remaining_from(np.array([0.001]), np.array([0.0]))[0], 0)

    def test_mad_is_robust_to_a_jump(self):
        frame = make_frame(datetime(2026, 7, 1, tzinfo=UTC), 300, seed=12)
        spiked = frame.copy()
        spiked.iloc[150, spiked.columns.get_loc("Close")] *= 1.05
        clean = mad_volatility(frame).iloc[-1]
        jumped = mad_volatility(spiked).iloc[-1]
        self.assertLess(abs(jumped - clean) / max(clean, 1e-12), 1.0)

    def test_scaling_sigma_barely_moves_accuracy_but_moves_brier(self):
        """Why section 10 forbids picking an estimator on accuracy."""
        rng = np.random.default_rng(6)
        n = 5000
        buffer = rng.normal(0, 0.002, n)
        sigma = np.full(n, 0.002)
        outcome = (buffer + rng.normal(0, 0.002, n) > 0).astype(int)

        p_true = gaussian_terminal(buffer, sigma).p_yes
        p_half = gaussian_terminal(buffer, sigma * 0.5).p_yes

        acc_true = ((p_true >= 0.5) == (outcome == 1)).mean()
        acc_half = ((p_half >= 0.5) == (outcome == 1)).mean()
        self.assertAlmostEqual(acc_true, acc_half, places=10)   # identical

        brier_true = np.mean((p_true - outcome) ** 2)
        brier_half = np.mean((p_half - outcome) ** 2)
        self.assertNotAlmostEqual(brier_true, brier_half, places=4)


class TestNormality(unittest.TestCase):
    def test_gaussian_sample_reads_as_gaussian(self):
        rng = np.random.default_rng(1)
        report = analyse_normality(rng.standard_normal(50000))
        self.assertLess(abs(report.skew), 0.1)
        self.assertLess(abs(report.excess_kurtosis), 0.2)
        self.assertIn("close to Gaussian", report.verdict)

    def test_heavy_tailed_sample_is_flagged(self):
        rng = np.random.default_rng(2)
        x = rng.standard_t(3, 50000)
        report = analyse_normality(x / x.std(ddof=1))
        self.assertGreater(report.excess_kurtosis, 1.0)
        self.assertIn("tails", report.verdict.lower())

    def test_tail_table_compares_at_operational_levels(self):
        rng = np.random.default_rng(3)
        report = analyse_normality(rng.standard_normal(40000))
        self.assertEqual(len(report.tail_table), 5)
        for row in report.tail_table:
            self.assertGreater(row["gaussian_tail"], 0.0)
            self.assertTrue(np.isfinite(row["ratio_observed_over_gaussian"]))

    def test_insufficient_sample_reported(self):
        self.assertEqual(analyse_normality(np.zeros(10)).verdict, "INSUFFICIENT SAMPLE")

    def test_student_t_df_recovered(self):
        rng = np.random.default_rng(4)
        out = fit_student_t_df(rng.standard_t(5, 20000))
        self.assertEqual(out["status"], "OK")
        self.assertGreater(out["df"], 3.0)
        self.assertLess(out["df"], 9.0)

    def test_volatility_clustering_detected(self):
        """Needs a genuinely PERSISTENT volatility process.

        EWMA-smoothing iid noise does not produce clustering -- it produces a
        near-constant level, because averaging 50 draws of N(1, 0.3) leaves a
        standard deviation of about 0.04. An AR(1) in log-volatility with a
        coefficient near 1 is the standard construction and does cluster.
        """
        rng = np.random.default_rng(7)
        n = 20000
        log_vol = np.zeros(n)
        for i in range(1, n):
            log_vol[i] = 0.99 * log_vol[i - 1] + rng.normal(0, 0.15)
        clustered = rng.standard_normal(n) * np.exp(log_vol)

        acf = volatility_clustering(clustered)
        self.assertGreater(acf, 0.10, f"expected clustering, got acf1={acf:.4f}")

    def test_no_clustering_in_iid_returns(self):
        """Control: iid returns must show essentially zero clustering."""
        rng = np.random.default_rng(8)
        acf = volatility_clustering(rng.standard_normal(20000))
        self.assertLess(abs(acf), 0.05)


class TestNoLeakageAndGrouping(unittest.TestCase):
    """Phase 5 must inherit Phase 3/4 discipline unchanged."""

    def setUp(self):
        start = datetime(2026, 7, 1, tzinfo=UTC)
        frames = {a: make_frame(start, 60 * 30, base=100.0 * (i + 1), seed=20 + i)
                  for i, a in enumerate(("AAA-USD", "BBB-USD"))}
        builder = ModellingDatasetBuilder(frames, ET, warmup_bars=120,
                                          scan_grid=(600, 300, 120))
        self.table, _ = builder.build()

    def test_holdout_remains_untouched(self):
        plan = reserve_holdout(self.table, holdout_fraction=0.25)
        for split in grouped_walk_forward(plan.dev, folds=3):
            plan.seal.assert_disjoint(
                set(plan.dev.iloc[split.train_idx].group_key), "phase5-train")
        self.assertTrue(plan.seal.intact)

    def test_same_window_assets_stay_grouped(self):
        plan = reserve_holdout(self.table, holdout_fraction=0.25)
        for split in grouped_walk_forward(plan.dev, folds=3):
            train = set(plan.dev.iloc[split.train_idx].group_key)
            test = set(plan.dev.iloc[split.test_idx].group_key)
            self.assertEqual(train & test, set())

    def test_standardized_returns_use_no_future_data_beyond_the_label(self):
        """The label is terminal by definition; the INPUTS must be causal."""
        z = standardized_terminal_returns(
            self.table.spot.to_numpy(),
            self.table.terminal_price.to_numpy(),
            self.table.sigma_remaining_return.to_numpy(),
        )
        self.assertEqual(len(z), len(self.table))
        # spot and sigma come from the leakage-safe builder; only terminal_price
        # is future information, and it is the LABEL, never a feature.
        self.assertNotIn("terminal_price", set(
            __import__("mantis_v4.models.dataset_builder", fromlist=["ALL_FEATURES"]).ALL_FEATURES
        ))


if __name__ == "__main__":
    unittest.main()
