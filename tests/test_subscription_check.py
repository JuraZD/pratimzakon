"""
Unit testovi za subscription_check job.
Koriste mock objekte — ne trebaju pravu DB konekciju.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import date, timedelta

# Dodaj backend direktorij u path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../backend"))


def make_mock_user(days_offset, email="test@example.com"):
      """Pomocna funkcija za kreiranje mock korisnika."""
      user = MagicMock()
      user.email = email
      user.subscription_end = date.today() + timedelta(days=days_offset)
      user.subscription_status = "active"
      user.keyword_limit = 50
      user.plan = "pro"
      return user


def run_with_mock_db(users):
      """Pokrece run() s mock bazom koja vraca zadane korisnike."""
      mock_db = MagicMock()
      mock_db.query.return_value.filter.return_value.all.return_value = users

    with patch("app.database.SessionLocal", return_value=mock_db):
              from app.jobs.subscription_check import run
              run()

    return mock_db


# ---------------------------------------------------------------------------
# Test 1: Istekla pretplata — downgrade na free
# ---------------------------------------------------------------------------

def test_expired_subscription_downgrade():
      """Korisnik cija je pretplata istekla mora biti downgrade-an na free."""
      user = make_mock_user(days_offset=-1)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [user]

    with patch("app.database.SessionLocal", return_value=mock_db):
              # reimportaj kako bi patch bio aktivan
              import importlib
              import app.jobs.subscription_check as sc
              importlib.reload(sc)
              sc.run()

    assert user.subscription_status == "expired"
    assert user.plan == "free"
    assert user.keyword_limit == 7
    mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: Korisnik bez subscription_end — preskoci
# ---------------------------------------------------------------------------

def test_no_subscription_end_skips_user():
      """Korisnik bez subscription_end datuma mora biti preskocen."""
      user = MagicMock()
      user.subscription_end = None
      user.subscription_status = "active"

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [user]

    with patch("app.database.SessionLocal", return_value=mock_db):
              import importlib
              import app.jobs.subscription_check as sc
              importlib.reload(sc)
              sc.run()

    # Status nije smio biti promijenjen
    assert user.subscription_status == "active"


# ---------------------------------------------------------------------------
# Test 3: Pretplata istice za 5 dana — admin notifikacija
# ---------------------------------------------------------------------------

def test_expiring_in_5_days_notifies_admin():
      """Korisnik cija pretplata istice za tocno 5 dana mora pokrenuti admin alert."""
      user = make_mock_user(days_offset=5, email="expiring@example.com")

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [user]

    with patch("app.database.SessionLocal", return_value=mock_db):
              import importlib
              import app.jobs.subscription_check as sc
              importlib.reload(sc)
              with patch.object(sc, "_notify_admin") as mock_notify:
                            sc.run()

          mock_notify.assert_called_once_with(["expiring@example.com"])


# ---------------------------------------------------------------------------
# Test 4: Pretplata jos vrijedi — nema promjena
# ---------------------------------------------------------------------------

def test_active_subscription_unchanged():
      """Korisnik s aktivnom pretplatom (vise od 5 dana) ne smije biti promijenjen."""
      user = make_mock_user(days_offset=30)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [user]

    with patch("app.database.SessionLocal", return_value=mock_db):
              import importlib
              import app.jobs.subscription_check as sc
              importlib.reload(sc)
              with patch.object(sc, "_notify_admin") as mock_notify:
                            sc.run()

          assert user.subscription_status == "active"
    mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: Vise korisnika — mijesani scenarij
# ---------------------------------------------------------------------------

def test_multiple_users_mixed():
      """Mijesani scenarij: expired, expiring soon i aktivni korisnici."""
      expired_user = make_mock_user(days_offset=-5, email="expired@example.com")
      expiring_user = make_mock_user(days_offset=5, email="soon@example.com")
      active_user = make_mock_user(days_offset=20, email="active@example.com")

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [
              expired_user, expiring_user, active_user
    ]

    with patch("app.database.SessionLocal", return_value=mock_db):
              import importlib
              import app.jobs.subscription_check as sc
              importlib.reload(sc)
              with patch.object(sc, "_notify_admin") as mock_notify:
                            sc.run()

          assert expired_user.subscription_status == "expired"
    assert expired_user.plan == "free"
    assert active_user.subscription_status == "active"
    mock_notify.assert_called_once_with(["soon@example.com"])


# ---------------------------------------------------------------------------
# Test 6: DB greska — session se uvijek zatvori
# ---------------------------------------------------------------------------

def test_db_session_always_closed():
      """DB sesija mora biti zatvorena cak i ako query baci iznimku."""
      mock_db = MagicMock()
      mock_db.query.side_effect = Exception("DB connection lost")

    with patch("app.database.SessionLocal", return_value=mock_db):
              import importlib
              import app.jobs.subscription_check as sc
              importlib.reload(sc)
              with pytest.raises(Exception):
                            sc.run()

          mock_db.close.assert_called_once()
