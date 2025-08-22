import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, StaleElementReferenceException
import json
import os
import csv
import re
import logging
import random

# --- 定义文件名 ---
PROGRESS_FILE = 'crawl_progress.json'
PENDING_DOCTORS_FILE = 'pending_doctors.json'
DOCTORS_CSV_DIR = 'hospital_doctors_data'
HOSPITALS_OUTPUT_FILE = 'hospitals_info.csv'
LOG_FILE = 'scraper.log'

# --- 定义CSV文件的表头 ---
DOCTORS_CSV_HEADERS = ['姓名', '职称', '医院', '主科室', '子科室', '简介', '擅长', '医生页链接', '头像链接']
HOSPITALS_CSV_HEADERS = ['医院序号', '医院名称', 'Logo链接', '标签', '医院介绍', '医院官网', '医院页面链接']


# --- 日志配置 ---
def setup_logging(level=logging.INFO):
    """配置日志记录器，同时输出到控制台和文件"""
    logger = logging.getLogger()
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(level)
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(module)s:%(lineno)d: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    file_handler = logging.FileHandler(LOG_FILE, 'a', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


# --- 进度和任务清单管理函数 (这部分无需修改) ---
def load_progress():
    """加载完整的爬取进度，包括医院范围和当前位置"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logging.warning(f"'{PROGRESS_FILE}' 文件损坏或为空, 将使用默认配置.")
                pass
    return {
        "hospital_range": "1-10099",  # 默认范围可以设大一些
        "current_hospital_id": 1,
        "main_dept_index": 0,
        "sub_dept_index": 0
    }


def save_progress(hospital_id, main_index, sub_index, hospital_range):
    """保存所有进度信息"""
    progress = {
        "hospital_range": hospital_range,
        "current_hospital_id": hospital_id,
        "main_dept_index": main_index,
        "sub_dept_index": sub_index
    }
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)


def save_pending_doctors(targets):
    with open(PENDING_DOCTORS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'targets': targets}, f, indent=2)


def load_pending_doctors():
    if os.path.exists(PENDING_DOCTORS_FILE):
        with open(PENDING_DOCTORS_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f).get('targets', [])
            except json.JSONDecodeError:
                return []
    return []


def remove_doctor_from_pending(url):
    pending_targets = load_pending_doctors()
    updated_targets = [target for target in pending_targets if target.get('url') != url]
    save_pending_doctors(updated_targets)


# --- 文件和数据加载函数 ---
def load_existing_links_from_csv(filepath, link_column):
    """通用函数，从指定的CSV文件加载链接"""
    # --- [优化] --- 检查并创建目录
    directory = os.path.dirname(filepath)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)

    if not os.path.exists(filepath): return set()
    links = set()
    try:
        with open(filepath, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if link_column in row and row[link_column]:
                    links.add(row[link_column])
        logging.info(f"从 '{os.path.basename(filepath)}' 加载了 {len(links)} 条已存在的链接.")
    except Exception as e:
        logging.error(f"读取CSV '{filepath}' 时出错: {e}")
    return links


def append_to_csv(data_dict, filepath, headers):
    """通用函数，追加数据到指定的CSV文件"""
    file_exists = os.path.exists(filepath)
    try:
        with open(filepath, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists: writer.writeheader()
            writer.writerow(data_dict)
    except IOError as e:
        logging.error(f"写入CSV文件 '{filepath}' 失败: {e}")


def sanitize_filename(filename):
    """移除文件名中的非法字符，并将空格替换为下划线"""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    sanitized = sanitized.replace(" ", "_")
    return sanitized


def get_hospital_csv_path(output_dir, hospital_id, hospital_name):
    """根据医院ID和名称生成专属的CSV文件路径"""
    sanitized_name = sanitize_filename(hospital_name)
    filename = f"hospital_{hospital_id}_{sanitized_name}.csv"
    return os.path.join(output_dir, filename)


# --- Selenium核心功能函数 (这部分无需修改) ---
def scrape_hospital_info(driver, hospital_id):
    hospital_url = f"https://www.youlai.cn/yyk/hospindex/{hospital_id}/"
    driver.get(hospital_url)
    info = {
        '医院序号': hospital_id, '医院名称': 'N/A', 'Logo链接': 'N/A',
        '标签': 'N/A', '医院介绍': 'N/A', '医院官网': 'N/A',
        '医院页面链接': hospital_url
    }
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'nameTag--J1Jna')]")))
        if "医院未找到" in driver.title or "404" in driver.title:
            logging.warning(f"医院ID {hospital_id} 无效或页面不存在.")
            info['医院名称'] = f"无效ID_{hospital_id}"
            return info, False
        info['医院名称'] = driver.find_element(By.XPATH, "//h1[contains(@class, 'name--uPsBN')]").text
        try:
            info['Logo链接'] = driver.find_element(By.XPATH,
                                                   "//div[contains(@class, 'logo--tbtwr')]//img").get_attribute('src')
        except NoSuchElementException:
            pass
        try:
            tags = driver.find_elements(By.XPATH, "//ul[contains(@class, 'tags--7DM1e')]//span")
            info['标签'] = ','.join([tag.text for tag in tags])
        except NoSuchElementException:
            pass
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'lineClamp__3')]")))
            info['医院介绍'] = driver.find_element(By.XPATH, "//div[contains(@class, 'lineClamp__3')]").text
        except (NoSuchElementException, TimeoutException):
            pass
        try:
            website_element = driver.find_element(By.XPATH, "//span[text()='医院官网']/following-sibling::div")
            info['医院官网'] = driver.execute_script("""return arguments[0].firstChild.textContent;""",
                                                     website_element).strip()
        except NoSuchElementException:
            pass
        return info, True
    except TimeoutException:
        logging.warning(f"访问医院ID {hospital_id} 页面超时.")
        info['医院名称'] = f"访问超时_{hospital_id}"
        return info, False


def get_doctor_details(driver, doctor_url, main_dept, fallback_avatar_src, hospital_name):
    driver.execute_script("window.open(arguments[0]);", doctor_url)
    driver.switch_to.window(driver.window_handles[-1])
    doctor_info = {"姓名": "N/A", "职称": "N/A", "医院": hospital_name, "主科室": main_dept, "子科室": "N/A",
                   "简介": "N/A", "擅长": "N/A", "医生页链接": doctor_url, "头像链接": "N/A"}
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//section[contains(@class, 'doctorInfoContainer')]")))
        try:
            doctor_info["姓名"] = driver.find_element(By.XPATH, "//span[@class='doc-name']").get_attribute(
                'textContent').strip()
        except NoSuchElementException:
            pass
        try:
            doctor_info["职称"] = driver.find_element(By.XPATH,
                                                      "//div[contains(@class, 'doctorInfo--')]//h3/a/span").text
        except NoSuchElementException:
            pass
        try:
            doctor_info["子科室"] = driver.find_element(By.XPATH, "//div[@class='doc-dept']").text
        except NoSuchElementException:
            pass
        try:
            intro_element = driver.find_element(By.XPATH, "//div[contains(@class, 'doctorInfoExtraIntro')]")
            doctor_info["简介"] = driver.execute_script("return arguments[0].textContent.replace('简介：', '').trim()",
                                                        intro_element)
        except NoSuchElementException:
            pass
        try:
            skill_element = driver.find_element(By.XPATH, "//div[contains(@class, 'doctorInfoExtraSkill')]")
            doctor_info["擅长"] = driver.execute_script("return arguments[0].textContent.replace('擅长：', '').trim()",
                                                        skill_element)
        except NoSuchElementException:
            pass
        try:
            avatar_element = driver.find_element(By.XPATH, "//div[contains(@class, 'avatarBox--gNp0Z')]//img")
            doctor_info["头像链接"] = avatar_element.get_attribute('src')
        except NoSuchElementException:
            pass
        if not doctor_info["头像链接"] or "N/A" in doctor_info["头像链接"]:
            doctor_info["头像链接"] = fallback_avatar_src
    except TimeoutException:
        logging.warning(f"医生详情页面加载超时: {doctor_url}")
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
    return doctor_info


# --- [优化] --- 使用新的、滚动更平滑的医生目标获取函数
def get_doctor_targets_optimized(driver: WebDriver, existing_doctor_links_this_hospital: set):
    """
    【优化版】高效获取新医生的URL和头像SRC，并保证从上到下顺序处理。
    """
    doctor_block_selector = (By.XPATH, "//a[contains(@class, 'block--Ux6NX')]")

    # 步骤 1: 滚动页面加载所有医生DOM
    logging.info("    滚动页面以加载所有医生...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)  # 等待懒加载内容
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    logging.info("    所有医生DOM加载完毕。")

    # 步骤 2: 获取所有医生元素，这是按页面顺序排列的
    try:
        all_blocks = driver.find_elements(*doctor_block_selector)
        if not all_blocks:
            logging.warning("    页面上未找到任何医生信息。")
            return []
        logging.info(f"    页面共找到 {len(all_blocks)} 名医生，开始筛选和处理...")
    except TimeoutException:
        logging.warning("    页面上未找到任何医生信息。")
        return []

    # 步骤 3: 顺序遍历，过滤出新医生并处理
    new_targets = []
    new_doctor_count = 0
    driver.execute_script("window.scrollTo(0, 0);")  # 回到顶部准备顺序处理
    time.sleep(0.5)

    for i, block in enumerate(all_blocks):
        try:
            url = block.get_attribute('href')
            if not url or url in existing_doctor_links_this_hospital:
                continue

            new_doctor_count += 1
            avatar_src = "N/A"
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", block)
            time.sleep(0.4)

            img_element = block.find_element(By.XPATH, ".//img")
            possible_attrs = ['src', 'data-src', 'data-original', 'data-url']
            for attr in possible_attrs:
                src_value = img_element.get_attribute(attr)
                if src_value and "placeholder" not in src_value and "base64" not in src_value:
                    avatar_src = src_value
                    break

            new_targets.append({'url': url, 'avatar_src': avatar_src})

        except (NoSuchElementException, StaleElementReferenceException) as e:
            logging.warning(f"    处理第 {i + 1} 个医生时出错: {e}，跳过。")
            continue

    if new_doctor_count > 0:
        logging.info(f"    发现并处理了 {new_doctor_count} 名新医生。")
    else:
        logging.info("    该科室没有需要抓取的新医生。")

    return new_targets


def main():
    setup_logging()

    # --- [优化] --- 浏览器选项设置
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/116.0',
    ]
    options = webdriver.ChromeOptions()
    random_user_agent = random.choice(USER_AGENTS)
    options.add_argument(f'--user-agent={random_user_agent}')
    logging.info(f"本次使用的User-Agent: {random_user_agent}")

    # options.add_argument('--headless')
    # options.add_argument('--window-size=1920,1080')
    options.add_argument('--start-maximized')

    progress = load_progress()
    hospital_range_str = progress['hospital_range']
    start_hospital_id = progress['current_hospital_id']

    try:
        start_range, end_range = map(int, hospital_range_str.split('-'))
        logging.info(f"计划处理医院范围: {start_range} - {end_range}")
    except (ValueError, KeyError):
        logging.error("医院范围格式错误或不存在，请在crawl_progress.json中设置为 'hospital_range': '开始-结束' 格式。")
        return

    start_main_idx = progress['main_dept_index'] if start_hospital_id == progress.get('current_hospital_id',
                                                                                      start_hospital_id) else 0
    start_sub_idx = progress['sub_dept_index'] if start_hospital_id == progress.get('current_hospital_id',
                                                                                    start_hospital_id) else 0

    # --- [优化] --- 不再全局加载所有医生链接，只加载医院信息链接
    existing_hospital_links = load_existing_links_from_csv(HOSPITALS_OUTPUT_FILE, '医院页面链接')
    pending_targets = load_pending_doctors()

    if pending_targets:
        logging.info(f"检测到 {len(pending_targets)} 名医生待处理，将从中断处恢复...")
    elif start_hospital_id > start_range or start_main_idx > 0 or start_sub_idx > 0:
        logging.info(f"检测到上次进度，将从医院ID {start_hospital_id} 恢复...")

    chromedriver_path = '/Users/qkb/Desktop/mycode/my_test_code/my_code/python_spider/chromedriver-mac-arm64/chromedriver'
    service = Service(executable_path=chromedriver_path)

    # options.add_argument('--headless')  # 启用无头模式
    # options.add_argument('--window-size=1920,1080')  # 建议设置一个窗口大小，避免某些元素因窗口太小而找不到

    # 在无头模式下，'--start-maximized' 可能无效，建议用window-size替代
    options.add_argument('--start-maximized')
    driver = webdriver.Chrome(service=service, options=options)

    try:
        for hospital_id in range(start_hospital_id, end_range + 1):
            logging.info(f"{'=' * 20} 开始处理医院 ID: {hospital_id} {'=' * 20}")
            save_progress(hospital_id, 0, 0, hospital_range_str)

            hospital_page_url = f"https://www.youlai.cn/yyk/hospindex/{hospital_id}/"
            current_hospital_name = "N/A"
            if hospital_page_url not in existing_hospital_links:
                hospital_info, success = scrape_hospital_info(driver, hospital_id)
                append_to_csv(hospital_info, HOSPITALS_OUTPUT_FILE, HOSPITALS_CSV_HEADERS)
                current_hospital_name = hospital_info['医院名称']
                if not success: continue
            else:
                logging.info(f"医院ID {hospital_id} 的信息已存在，跳过医院信息抓取.")
                driver.get(hospital_page_url)
                try:
                    current_hospital_name = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, "//h1[contains(@class, 'name--uPsBN')]"))).text
                except TimeoutException:
                    logging.warning("无法获取已存在医院的名称，将使用 N/A。")

            if "N/A" in current_hospital_name or "无效ID" in current_hospital_name or "访问超时" in current_hospital_name:
                logging.warning(f"医院名称无效 ({current_hospital_name})，跳过该医院的医生抓取。")
                continue

            hospital_doctor_csv_path = get_hospital_csv_path(DOCTORS_CSV_DIR, hospital_id, current_hospital_name)

            # --- [优化] --- 在这里加载当前医院已存在的医生链接
            existing_links_this_hospital = load_existing_links_from_csv(hospital_doctor_csv_path, '医生页链接')

            doctor_list_url = f"https://www.youlai.cn/yyk/hospindex/{hospital_id}/doctorlist.html"
            driver.get(doctor_list_url)
            newly_scraped_doctors = 0

            try:
                main_dept_selector = (By.XPATH,
                                      "//div[text()='科室筛选']/following-sibling::div[contains(@class, 'rightContent')]//div[contains(@class, 'box--')]")
                WebDriverWait(driver, 10).until(EC.presence_of_element_located(main_dept_selector))
                main_departments = driver.find_elements(*main_dept_selector)
            except TimeoutException:
                logging.warning("该医院没有科室筛选模块，跳过医生抓取。")
                continue

            for i in range(start_main_idx, len(main_departments)):
                main_departments = driver.find_elements(*main_dept_selector)
                if "全部" in main_departments[i].text: continue

                main_dept_name = main_departments[i].text
                logging.info(f"正在处理主科室 ({i + 1}/{len(main_departments)}): {main_dept_name}")
                driver.execute_script("arguments[0].click();", main_departments[i])
                time.sleep(random.uniform(1.5, 3.5))

                sub_dept_selector = (By.XPATH,
                                     "//div[contains(@class, 'levelTwo--Ntq0X')]/div[contains(@class, 'text--')]")
                try:
                    sub_departments = driver.find_elements(*sub_dept_selector)
                except (NoSuchElementException, TimeoutException):
                    sub_departments = []

                current_sub_start_idx = start_sub_idx if i == start_main_idx else 0
                departments_to_process = []
                if not sub_departments or len(sub_departments) <= 1:
                    departments_to_process.append(("N/A", -1))  # 使用-1作为索引表示无子科室
                else:
                    for j in range(current_sub_start_idx, len(sub_departments)):
                        if sub_departments[j].text not in ["不限", "全部"]:
                            departments_to_process.append((sub_departments[j].text, j))

                for sub_dept_name, sub_dept_index in departments_to_process:
                    if sub_dept_name != "N/A":
                        sub_depts_fresh = driver.find_elements(*sub_dept_selector)
                        if sub_dept_index < len(sub_depts_fresh):
                            logging.info(f"  -> 子科室 ({sub_dept_index + 1}/{len(sub_departments)}): {sub_dept_name}")
                            driver.execute_script("arguments[0].click();", sub_depts_fresh[sub_dept_index])
                            time.sleep(random.uniform(1.5, 3.5))
                        else:
                            logging.warning("子科室元素刷新后索引越界，跳过。")
                            continue

                    if not pending_targets:
                        save_progress(hospital_id, i, sub_dept_index, hospital_range_str)
                        # --- [优化] --- 调用新的优化函数，并传入当前医院的链接集合
                        pending_targets = get_doctor_targets_optimized(driver, existing_links_this_hospital)
                        save_pending_doctors(pending_targets)

                    if pending_targets:
                        logging.info(f"    开始处理 {len(pending_targets)} 名待抓取医生...")
                        for target in list(pending_targets):
                            doctor_data = get_doctor_details(driver, target['url'], main_dept_name,
                                                             target['avatar_src'], current_hospital_name)
                            append_to_csv(doctor_data, hospital_doctor_csv_path, DOCTORS_CSV_HEADERS)
                            # --- [优化] --- 更新内存中的集合
                            existing_links_this_hospital.add(doctor_data['医生页链接'])
                            newly_scraped_doctors += 1
                            logging.info(f"    已抓取并保存: {doctor_data['姓名']}, {doctor_data['职称']}")
                            remove_doctor_from_pending(target['url'])
                        pending_targets = []

                start_sub_idx = 0

            start_main_idx = 0

            if newly_scraped_doctors > 0:
                logging.info(
                    f"医院ID {hospital_id} 本次共抓取 {newly_scraped_doctors} 条新医生信息到 '{os.path.basename(hospital_doctor_csv_path)}'。")
            else:
                logging.info(f"医院ID {hospital_id} 本次未抓取到任何新的医生信息。")

            # --- [优化] --- 抓完一个医院后可以增加一个稍长的随机暂停
            logging.info("进入医院间歇期...")
            time.sleep(random.uniform(5, 15))


    except Exception as e:
        logging.exception(f"发生未知错误: {e}")
        logging.error("程序意外中断。当前进度已保存，下次启动可恢复。")
    finally:
        driver.quit()
        logging.info("浏览器已关闭。")


if __name__ == "__main__":
    main()