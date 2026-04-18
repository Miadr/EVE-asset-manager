import logging
import os
import sys

def setup_logger(name, log_file):
    # For a portable version, we store logs in the current working directory -> 'data/logs'
    log_dir = os.path.join(os.getcwd(), 'data', 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if setup_logger is called multiple times
    if not logger.handlers:
        file_handler = logging.FileHandler(os.path.join(log_dir, log_file), encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
    return logger
