#!/usr/bin/env python3
"""
MinIO SSL连接问题修复脚本

解决容器环境中MinIO SSL连接失败的问题
"""

import os
import ssl
import urllib3
from urllib3.exceptions import InsecureRequestWarning

def fix_minio_ssl_issues():
    """修复MinIO SSL连接问题"""
    
    # 1. 禁用SSL警告（生产环境临时解决方案）
    urllib3.disable_warnings(InsecureRequestWarning)
    
    # 2. 设置SSL上下文为不验证证书（仅用于内网环境）
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    # 3. 设置环境变量
    os.environ['PYTHONHTTPSVERIFY'] = '0'
    os.environ['CURL_CA_BUNDLE'] = ''
    os.environ['REQUESTS_CA_BUNDLE'] = ''
    
    print("MinIO SSL配置已修复")
    return ssl_context

if __name__ == "__main__":
    fix_minio_ssl_issues()