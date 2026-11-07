from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *


class SocksArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="action",
                type=ParameterType.ChooseOne,
                choices=["start", "stop"],
                description="Start or stop SOCKS5 proxy",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
            CommandParameter(
                name="port",
                type=ParameterType.Number,
                description="SOCKS proxy port on Mythic server",
                default_value=1080,
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line) > 0 and self.command_line[0] == "{":
            self.load_args_from_json_string(self.command_line)
        elif len(self.command_line) > 0:
            parts = self.command_line.split()
            if len(parts) >= 1:
                self.add_arg("action", parts[0])
            if len(parts) >= 2:
                self.add_arg("port", int(parts[1]))
        else:
            raise ValueError("Usage: socks start <port> / socks stop <port>")


class SocksCommand(CommandBase):
    cmd = "socks"
    needs_admin = False
    help_cmd = "socks start 7000 / socks stop 7000"
    description = "Start or stop SOCKS5 proxy through this callback"
    version = 1
    author = "@Lavender-exe"
    argument_class = SocksArguments
    attackmapping = ["T1090"]
    supported_ui_features = ["callback_table:socks"]

    async def create_go_tasking(
        self, taskData: MythicCommandBase.PTTaskMessageAllData
    ) -> MythicCommandBase.PTTaskCreateTaskingMessageResponse:
        response = MythicCommandBase.PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=True,
        )
        action = taskData.args.get_arg("action")
        port = taskData.args.get_arg("port")

        if action == "start":
            proxy_resp = await SendMythicRPCProxyStartCommand(
                MythicRPCProxyStartMessage(
                    TaskID=taskData.Task.ID,
                    PortType=CALLBACK_PORT_TYPE_SOCKS,
                    LocalPort=port,
                )
            )
            if proxy_resp.Success:
                response.DisplayParams = f"Started SOCKS5 proxy on port {port}"
            else:
                response.Success = False
                response.Error = f"Failed to start SOCKS proxy: {proxy_resp.Error}"
        elif action == "stop":
            proxy_resp = await SendMythicRPCProxyStopCommand(
                MythicRPCProxyStopMessage(
                    TaskID=taskData.Task.ID,
                    PortType=CALLBACK_PORT_TYPE_SOCKS,
                    Port=port,
                )
            )
            if proxy_resp.Success:
                response.DisplayParams = f"Stopped SOCKS5 proxy on port {port}"
            else:
                response.Success = False
                response.Error = f"Failed to stop SOCKS proxy: {proxy_resp.Error}"

        return response

    async def process_response(
        self, task: PTTaskMessageAllData, response: any
    ) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(
            TaskID=task.Task.ID, Success=True
        )
        return resp
