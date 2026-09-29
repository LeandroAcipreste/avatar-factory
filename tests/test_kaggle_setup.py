import pytest
from scripts.configure_kaggle import merge_env, validate_token
from app.gpu_dispatch import KaggleGpuDispatcher, KaggleGpuSettings


@pytest.mark.parametrize('token', ['', '***', 'oc-sent-v2.fake.end', 'token\nINJECT=x', 'token with space', '$expand'])
def test_reject_unsafe_token(token):
    with pytest.raises(ValueError):
        validate_token(token)


def test_merge_preserves_unrelated_and_disables_gpu():
    original = '# comment\nHOST=example\nKAGGLE_API_TOKEN=***\nKAGGLE_GPU_ENABLED=true\nOTHER=value'
    result = merge_env(original, 'test-only-token')
    assert '# comment\nHOST=example\n' in result
    assert 'OTHER=value\n' in result
    assert 'KAGGLE_API_TOKEN=test-only-token\n' in result
    assert 'KAGGLE_GPU_ENABLED=false\n' in result
    assert '***' not in result


def test_duplicate_sensitive_keys_removed():
    result = merge_env('KAGGLE_API_TOKEN=old\nexport KAGGLE_API_TOKEN=other\n', 'fake-token')
    assert result.count('KAGGLE_API_TOKEN=') == 1


@pytest.mark.parametrize('token', ['***', 'oc-sent-v2.fake.end'])
def test_placeholder_fails_before_any_dispatch(token):
    settings = KaggleGpuSettings(True, 'user', token, 'user/worker', 'input')
    result = KaggleGpuDispatcher(settings, executor=lambda *_: pytest.fail('Must not call network')).dispatch({})
    assert result.status == 'gpu_dispatch_failed'
