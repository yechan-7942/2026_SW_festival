"""시설 추가 시나리오의 6개 상태·정책 연결·브라우저 오류 검증 및 화면 샘플."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main(url=None, output_dir='outputs/facility_scenario'):
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1050}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url or (Path.cwd() / 'outputs/dashboard.html').as_uri(), wait_until='load')
        assert '구룡포읍' in page.locator('#facility-selection').inner_text()
        for kind in ['hospital', 'pharmacy']:
            page.locator('#facility-kind').select_option(kind)
            for km in ['1', '3', '5']:
                page.locator('#facility-distance').select_option(km)
                assert page.locator('#facility-rows tr').count() == 29
                assert '없음' in page.locator('#facility-effect').inner_text()
                if km == '3':
                    expected = '0.00223185' if kind == 'hospital' else '0.00196928'
                    assert expected in page.locator('#facility-summary').inner_text()
                    page.locator('#facility-evidence').screenshot(path=str(target / f'{kind}_screen.png'))
        page.locator('#facility-kind').select_option('hospital')
        page.locator('#facility-distance').select_option('3')
        page.locator('#search').fill('구룡포')
        card = page.locator('#card-grid .card.visible')
        assert card.count() == 1
        assert '0.00223185' in card.inner_text()
        assert '현장 확인 후 통역·예약·동행 지원 검토' in card.inner_text()
        card.screenshot(path=str(target / 'policy_card_screen.png'))
        # 기존 비교와 입지 UI도 정상 작동한다.
        page.locator('#comparison-metric').select_option('count')
        assert page.locator('#comparison-body tr').count() == 29
        page.locator('#sim-site').select_option(label='구룡포읍 (추천 1위)')
        assert '0.883 → 0.851' in page.locator('#sim-summary').inner_text()
        assert not errors, errors
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        browser.close()
    result = {'scenario_states': 6, 'rows_per_state': 29, 'browser_errors': errors,
              'target': url or 'outputs/dashboard.html',
              'policy_link': 'passed', 'existing_controls': 'passed', 'mobile_overflow': 'passed'}
    (target / 'ui_check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='지정하지 않으면 로컬 대시보드 검사')
    parser.add_argument('--output-dir', default='outputs/facility_scenario')
    args = parser.parse_args()
    main(args.url, args.output_dir)
