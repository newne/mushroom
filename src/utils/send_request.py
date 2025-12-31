from loguru import logger
from requests import request
from tenacity import (
    retry,
    stop_after_attempt,
    stop_after_delay,
    wait_fixed,
    after_log,
    wait_random,
)


class SendRequest:
    def __init__(self):
        pass

    @retry(
        stop=(stop_after_delay(10) | stop_after_attempt(3)),
        wait=(wait_fixed(2) + wait_random(0, 2)),
        after=after_log(logger, logger.level("INFO").no),
        reraise=True,
    )
    def send_get_request(self, url, headers, payload):
        response = request("GET", url, headers=headers, data=payload, timeout=5)
        response.raise_for_status()  # 检查响应状态码
        if response.ok:
            return response
        else:
            logger.warning(
                "[0.0.0] Get接口调用失败！进行第{0}次重试，URL：{1},返回数据：{2}".format(
                    self.send_get_request.retry.statistics.get("attempt_number"),
                    url,
                    response.text,
                )
            )

            if self.send_get_request.retry.statistics.get("attempt_number") < 3:
                raise ValueError(
                    "Get接口调用失败！进行第{0}次重试，URL：{1},返回数据：{2}".format(
                        self.send_get_request.retry.statistics.get("attempt_number"),
                        url,
                        response.text,
                    )
                )

    @retry(
        stop=(stop_after_delay(10) | stop_after_attempt(3)),
        wait=(wait_fixed(2) + wait_random(0, 2)),
        after=after_log(logger, logger.level("INFO").no),
        reraise=True,
    )
    def send_post_request(self, url, headers, payload):
        response = request("POST", url, headers=headers, data=payload, timeout=650)
        response.raise_for_status()  # 检查响应状态码
        if response.ok:
            return response
        else:
            logger.warning(
                "[0.0.5] 历史数据接口调用失败！进行第{0}次重试，URL：{1},返回数据：{2}".format(
                    self.send_post_request.retry.statistics.get("attempt_number"),
                    url,
                    response.text,
                )
            )
            if self.send_post_request.retry.statistics.get("attempt_number") < 3:
                raise ValueError

    @retry(
        stop=(stop_after_delay(10) | stop_after_attempt(3)),
        wait=(wait_fixed(2) + wait_random(0, 2)),
        after=after_log(logger, logger.level("INFO").no),
        reraise=True,
    )
    def send_put_request(self, url, headers, payload):
        response = request("PUT", url, headers=headers, data=payload)
        if response.status_code == 200:
            return response
        else:
            logger.warning(
                "[0.0.2] PUT接口调用失败！进行第{0}次重试，URL：{1},返回数据：{2}".format(
                    self.send_put_request.retry.statistics.get("attempt_number"),
                    url,
                    response.text,
                )
            )
            if self.send_post_request.retry.statistics.get("attempt_number") < 3:
                raise ValueError
