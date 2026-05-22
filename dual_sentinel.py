#!/usr/bin/env python3
"""
Dual Universe Sentinel Runner

Runs both currency pairs and small cap pairs in parallel, tracks both,
generates side-by-side briefings.

Usage:
    python dual_sentinel.py                    # Run both universes
    python dual_sentinel.py --currencies-only  # Run currencies only
    python dual_sentinel.py --smallcaps-only   # Run small caps only

Configuration:
    Currencies: 
      - Johansen: 90% CI
      - Regime trigger: VIX > 18 (source/screen only)
      - Universe: 25 currency pairs
    
    Small Caps:
      - Johansen: 90% CI
      - Regime trigger: None (always source/screen)
      - Universe: 50 small cap stocks
"""

import os
import sys
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

# Add ShiftInnerV to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from shiftinnerv.sensors.regime_monitor import RegimeDetector, RegimeState


def run_universe(universe_name: str, universe_file: str, vix_level: float = None):
    """
    Run sentinel for a specific universe.
    
    Args:
        universe_name: 'currencies' or 'smallcaps'
        universe_file: Path to universe.yaml file
        vix_level: Current VIX (for regime-based decisions)
    
    Returns:
        (success: bool, briefing_path: str)
    """
    
    print(f"\n{'='*80}")
    print(f"Running {universe_name.upper()} universe")
    print(f"{'='*80}\n")
    
    # Check if VIX gate should skip currencies
    if universe_name == "currencies" and vix_level is not None and vix_level < 18:
        print(f"⏹ VIX {vix_level:.1f} < 18 — skipping currency pairs (calm regime)")
        print(f"   Currencies only source/screen when VIX > 18")
        return (False, None)
    
    # Copy universe file to main location temporarily
    main_universe = PROJECT_ROOT / "universe.yaml"
    backup_universe = PROJECT_ROOT / "universe.yaml.backup"
    
    try:
        # Backup current universe
        if main_universe.exists():
            import shutil
            shutil.copy(main_universe, backup_universe)
        
        # Copy target universe into place
        import shutil
        shutil.copy(universe_file, main_universe)
        
        # Run sentinel.py
        result = subprocess.run(
            [sys.executable, "sentinel.py"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print(f"✓ {universe_name.upper()} sentinel completed successfully")
            return (True, None)
        else:
            print(f"✗ {universe_name.upper()} sentinel failed:")
            print(result.stderr[-500:] if result.stderr else "No error output")
            return (False, None)
    
    finally:
        # Restore original universe
        if backup_universe.exists():
            import shutil
            shutil.move(backup_universe, main_universe)


def main():
    parser = argparse.ArgumentParser(description='Run dual-universe sentinel')
    parser.add_argument(
        '--currencies-only',
        action='store_true',
        help='Run currencies universe only'
    )
    parser.add_argument(
        '--smallcaps-only',
        action='store_true',
        help='Run small caps universe only'
    )
    
    args = parser.parse_args()
    
    print("\n" + "="*80)
    print("SHIFTINNERV — DUAL UNIVERSE SENTINEL")
    print("="*80)
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Get current VIX for regime decisions
    try:
        import logging
        from shiftinnerv.sensors.regime_monitor import RegimeDetector
        data_dir = os.environ.get('DATA_STORAGE_PATH', '/Volumes/Elessar/InnerShiftV_Data')
        detector = RegimeDetector(data_dir=data_dir)
        regime = detector.detect_regime(open_positions=[], logger=logging.getLogger())
        vix_level = regime.vix_level
    except Exception as e:
        print(f"⚠ Could not detect VIX ({str(e)[:50]}), continuing with default")
        vix_level = 17.0
    
    print(f"Current VIX: {vix_level:.1f}")
    print()
    
    # Determine which universes to run
    run_currencies = not args.smallcaps_only
    run_smallcaps = not args.currencies_only
    
    # Run universes
    results = {}
    
    if run_currencies:
        currencies_file = PROJECT_ROOT / "universe_currencies_only.yaml"
        if not currencies_file.exists():
            print(f"✗ Currency universe file not found: {currencies_file}")
            run_currencies = False
        else:
            results['currencies'] = run_universe('currencies', currencies_file, vix_level)
    
    if run_smallcaps:
        smallcaps_file = PROJECT_ROOT / "universe_smallcaps.yaml"
        if not smallcaps_file.exists():
            print(f"✗ Small caps universe file not found: {smallcaps_file}")
            run_smallcaps = False
        else:
            results['smallcaps'] = run_universe('smallcaps', smallcaps_file, vix_level)
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    if run_currencies:
        status = "✓" if results['currencies'][0] else "✗"
        print(f"{status} Currencies: {results['currencies'][0]}")
    
    if run_smallcaps:
        status = "✓" if results['smallcaps'][0] else "✗"
        print(f"{status} Small Caps: {results['smallcaps'][0]}")
    
    print()
    print("Briefings saved to: /Volumes/Elessar/InnerShiftV_Data/reports/")
    print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()


if __name__ == '__main__':
    main()
