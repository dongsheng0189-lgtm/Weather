import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

# workers=1：探測迴圈跑在 worker 內的背景執行緒，多個 worker 會各跑一份，
# 取樣就重複了。這個服務負載極低，一個 worker 綽綽有餘。
workers = 1
worker_class = "sync"
threads = 4
timeout = 60
accesslog = "-"
errorlog = "-"
loglevel = "info"
