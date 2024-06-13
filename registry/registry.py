import argparse
import json
import os
import socket
import threading
import time
from collections import defaultdict
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import List
from urllib.parse import urlparse, parse_qs


class Log:
    """运行日志"""

    def __init__(self):
        if not os.path.exists('./log'):
            os.makedirs('./log')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_filename = f'./log/{timestamp}.txt'

    def info(self, msg):
        """普通消息日志"""
        with open(self.log_filename, 'a') as log_file:
            log_file.write(f'[INFO] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} - {msg}\n')
        print(f'[INFO] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} - {msg}')

    def error(self, msg):
        """错误消息日志"""
        with open(self.log_filename, 'a') as log_file:
            log_file.write(f'[ERROR] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} - {msg}\n')
        print(f'[ERROR] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} - {msg}')


class InstanceMeta:
    """服务实例注册与发现使用的数据结构"""

    def __init__(self, protocol=None, host=None, port=None):
        self.protocol = protocol  # 服务使用的序列化与反序列化的消息格式，如json
        self.host = host  # 服务注册的ip地址
        self.port = port  # 服务注册的端口号
        self.status = None  # 服务注册状态，注销False，已注册状态True
        self.parameters = {}  # 服务注册时附加参数，扩展可在参数上设条件细化对服务实例的管理

    # 一些工具函数
    @staticmethod
    def from_dict(data):
        instance = InstanceMeta(
            protocol=data.get('protocol'),
            host=data.get('host'),
            port=data.get('port'),
        )
        instance.status = data.get('status')
        instance.parameters = data.get('parameters', {})
        return instance

    def to_dict(self):
        return {
            'protocol': self.protocol,
            'host': self.host,
            'port': self.port,
            'status': self.status,
            'parameters': self.parameters
        }

    def get_parameters(self):
        return self.parameters

    def add_parameters(self, parameters):
        self.parameters.update(parameters)
        return self

    def get_status(self):
        return self.status

    def set_status(self, status):
        self.status = status

    def __eq__(self, other):
        """定义比较两个服务实例是否相等的判定标准"""
        if not isinstance(other, InstanceMeta):
            return False
        return self.protocol == other.protocol and self.host == other.host and self.port == other.port

    def __hash__(self):
        """规定服务实例的哈希值的计算方式，用于实例位于字典/集合等哈希结构时的快速查找"""
        return hash((self.protocol, self.host, self.port))

    def __str__(self):
        """规定打印服务实例时的输出"""
        return (f"InstanceMeta(protocol={self.protocol}, host={self.host}, port={self.port}, "
                f"status={self.status}, parameters={self.parameters})")


class RegistryService:
    """注册中心服务类"""

    def __init__(self, logger: Log):
        self.proto2instances = defaultdict(list)  # 存不同序列化数据格式对应的服务实例
        self.ins2timestamp = defaultdict(int)  # 存各个服务实例的时间戳，用于心跳检测
        self.logger = logger  # 日志
        self._stop_event = threading.Event()
        self._health_thread = threading.Thread(target=self.loop_check_health)  # 心跳检测线程
        self._health_thread.start()

    def register(self, ins: InstanceMeta) -> InstanceMeta:
        """处理服务实例注册"""
        proto = ins.protocol
        if ins in self.proto2instances[proto]:
            self.logger.info(f"Register already exists instance=> {ins}")
            ins.set_status(True)
            old_time = self.ins2timestamp[ins]
            self.logger.info(f"Its last registered time: {datetime.fromtimestamp(old_time).strftime('%Y-%m-%d %H:%M:%S')}")
            self.ins2timestamp[ins] = int(time.time())
            new_time = self.ins2timestamp[ins]
            self.logger.info(f"Updated its timestamp: {datetime.fromtimestamp(new_time).strftime('%Y-%m-%d %H:%M:%S')}\n")
            return ins
        self.logger.info(f"Register instance=> {ins}\n")
        ins.set_status(True)
        self.proto2instances[proto].append(ins)
        self.ins2timestamp[ins] = int(time.time())
        return ins

    def unregister(self, ins: InstanceMeta) -> InstanceMeta:
        """处理服务实例注销"""
        proto = ins.protocol
        if ins not in self.proto2instances[proto]:
            self.logger.info(f"Unregister an instance not found=> {ins}\n")
            ins.set_status(False)
            return ins
        self.logger.info(f"Unregister instance=> {ins}\n")
        self.proto2instances[proto].remove(ins)
        del self.ins2timestamp[ins]
        ins.set_status(False)
        return ins

    def find_instances_by_protocol(self, protocol="json") -> List[InstanceMeta]:
        """根据序列化消息格式返回对应服务实例"""
        return self.proto2instances[protocol]

    def handle_check_health(self):
        """对服务实例进行健康检测"""
        cur_time = int(time.time())
        threshold = 10
        if not self.ins2timestamp:
            self.logger.info('Health check=> Instance list is empty\n')
        else:
            self.logger.info('Health check==================>')
            for ins, timestamp in list(self.ins2timestamp.items()):
                if cur_time - timestamp > threshold:
                    self.logger.info(
                        f"!!!Instance {ins} is unhealthy, last seen at {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}")
                    self.unregister(ins)
                else:
                    self.logger.info(
                        f"Instance {ins} is healthy, last seen at {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}")

    def stop(self):
        """停止心跳检测线程"""
        self._stop_event.set()  # 设置停止事件
        self._health_thread.join()  # 等待线程结束

    def loop_check_health(self):
        """定期健康检测，循环"""
        time.sleep(5)
        self.logger.info("健康检测已在后台开启")
        while not self._stop_event.is_set():
            self.handle_check_health()
            self._stop_event.wait(5)  # 等待5秒或直到事件被设置


class RequestHandler(BaseHTTPRequestHandler):
    """注册中心路由类"""

    def __init__(self, *args, **kwargs):
        self.registry_service = kwargs.pop('registry_service')  # 处理服务
        self.logger = kwargs.pop('logger')  # 日志
        super().__init__(*args, **kwargs)  # 父类默认初始化

    def log_message(self, format, *args):
        """重写log_message，将日志写到self.logger"""
        # self.logger.info("%s - %s\n" %(self.client_address[0], format % args))
        pass

    def do_POST(self):
        parsed_path = urlparse(self.path)
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        body = json.loads(post_data)

        if parsed_path.path == '/myRegistry/register':
            """服务注册路由"""
            instance_meta = InstanceMeta.from_dict(body)  # 获取注册实例

            registered_instance = self.registry_service.register(instance_meta)  # 处理注册服务
            self.respond(registered_instance.to_dict())  # 返回注册好的实例
        elif parsed_path.path == '/myRegistry/unregister':
            """服务注销路由"""
            instance_meta = InstanceMeta.from_dict(body)
            unregistered_instance = self.registry_service.unregister(instance_meta)
            self.respond(unregistered_instance.to_dict())

    def do_GET(self):
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)
        if parsed_path.path == '/myRegistry/findAllInstances':
            """服务发现路由，根据序列化数据格式请求"""
            protocol = query_params.get('proto', [None])[0]
            instances = self.registry_service.find_instances_by_protocol(protocol)
            self.respond([instance.to_dict() for instance in instances])

    def respond(self, data):
        """respond函数"""
        response = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(response)


def run(server_class=ThreadingHTTPServer, handler_class=RequestHandler, host='0.0.0.0', port=8081,
        registry_service=None,
        logger=None):
    if ':' in host:
        server_class.address_family = socket.AF_INET6
    else:
        server_class.address_family = socket.AF_INET

    server_address = (host, port)
    httpd = server_class(server_address,
                         lambda *args,
                                **kwargs: handler_class(*args,
                                                        registry_service=registry_service,
                                                        logger=logger,
                                                        **kwargs))

    if host == '0.0.0.0':
        hostname = socket.gethostname()
        ip_addr = socket.gethostbyname(hostname)
    else:
        ip_addr = host

    logger.info(f'Starting registry server on {ip_addr} port {port}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Main thread received KeyboardInterrupt, stopping...")
    finally:
        registry_service.stop()
        logger.info("Registry service stopped.")
        exit(-1)


if __name__ == '__main__':
    # 启动参数设置
    pars = argparse.ArgumentParser(description='Registry Center HTTP Server')
    pars.add_argument('-l', '--host', type=str, default='0.0.0.0',
                      help='注册中心监听的 IP 地址，同时支持 IPv4 和 IPv6，可以为空，默认监听所有 IP 地址')
    pars.add_argument('-p', '--port', type=int, required=True,
                      help='注册中心监听的端口号，不可为空')
    args = pars.parse_args()

    # 日志与注册中心服务实例创建
    logger = Log()
    rs = RegistryService(logger)

    # 启动注册中心
    run(host=args.host, port=args.port, registry_service=rs, logger=logger)