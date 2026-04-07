import asyncio
import os
import tempfile
from playwright.async_api import async_playwright

_BROWSER_PROFILE_DIR = os.path.join(tempfile.gettempdir(), "wmp_browser_profile_test")

async def test_livejournal():
    print("Starting Playwright Test for LiveJournal...")
    url = "https://vrotmnen0gi.livejournal.com/4898537.html"
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=_BROWSER_PROFILE_DIR,
            headless=False, # Make it visible for testing
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            args=["--disable-gpu", "--no-sandbox"]
        )
        
        # In launch_persistent_context, a default page is already created if we don't pass ignore_default_args
        page = context.pages[0] if context.pages else await context.new_page()
        
        print(f"Navigating to {url} ...")
        # Go to url, but wait for domcontentloaded instead of networkidle, 
        # as networkidle might never happen or take too long.
        response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        
        print(f"Response status: {response.status if response else 'None'}")
        
        # Wait 2.5 seconds like in our code
        print("Waiting 2.5 seconds...")
        await asyncio.sleep(2.5)
        
        print("Scanning for buttons...")
        consent_keywords = ["agree", "confirm", "yes", "accept", "continue",
                             "enter", "согласен", "да", "продолжить", "i'm 18", "18+",
                             "i am", "verify", "proceed"]
        
        patterns = [
            'button:has-text("Yes")', 'button:has-text("Confirm")', 'button:has-text("Agree")',
            'button:has-text("18+")', 'button:has-text("Продолжить")', 'button:has-text("Да")',
            'button:has-text("I Agree")', 'button:has-text("I agree")',
            'a:has-text("Да")'
        ]
        
        frames_to_search = [page] + list(page.frames)
        clicked = False
        
        for frame in frames_to_search:
            if clicked: break
            
            for selector in patterns:
                try:
                    btn = await frame.query_selector(selector)
                    if btn and await btn.is_visible():
                        print(f"Found selector! {selector} in frame {frame.url}")
                        await btn.click()
                        await page.wait_for_load_state("networkidle", timeout=8000)
                        clicked = True
                        break
                except Exception as e:
                    pass
            
            if not clicked:
                all_buttons = await frame.query_selector_all(
                    "button, a[role='button'], [type='button'], [type='submit'], input[type='button'], span:has-text('Да')"
                )
                for btn in all_buttons:
                    try:
                        if not await btn.is_visible(): continue
                        text = (await btn.inner_text()).strip().lower()
                        if any(kw in text for kw in consent_keywords):
                            print(f"Found via text scan! Text: '{text}' in frame {frame.url}")
                            await btn.click()
                            await page.wait_for_load_state("networkidle", timeout=8000)
                            clicked = True
                            break
                    except Exception:
                        pass
        
        print(f"Clicked something: {clicked}")
        print("Waiting 5 seconds to observe final page state...")
        await asyncio.sleep(5)
        print(f"Final URL: {page.url}")
        
        await context.close()

if __name__ == "__main__":
    asyncio.run(test_livejournal())
