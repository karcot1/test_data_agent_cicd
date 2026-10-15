# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess

import google.auth
import google.auth.exceptions
import pytest

from deployer import auth


def no_adc(*args, **kwargs):
    raise google.auth.exceptions.DefaultCredentialsError("no ADC")


def test_falls_back_to_gcloud_token(monkeypatch):
    monkeypatch.setattr(google.auth, "default", no_adc)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "tok\n")
    assert auth.get_credentials().token == "tok"


@pytest.mark.parametrize("gcloud_error", [
    FileNotFoundError("gcloud"),
    subprocess.CalledProcessError(1, "gcloud"),
])
def test_clear_error_without_adc_or_gcloud(monkeypatch, gcloud_error):
    def fail(*args, **kwargs):
        raise gcloud_error

    monkeypatch.setattr(google.auth, "default", no_adc)
    monkeypatch.setattr(subprocess, "check_output", fail)
    with pytest.raises(auth.AuthError, match="gcloud auth application-default login"):
        auth.get_credentials()
