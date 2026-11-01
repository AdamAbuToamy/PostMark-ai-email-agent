param([switch]$ValidateOnly,[switch]$ValidateLive,[ValidateSet('personal','university')][string]$ValidationAccount='personal')
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Windows.Forms,System.Drawing,System.Net.Http
$script:base=Split-Path -Parent $MyInvocation.MyCommand.Path
$script:account=if($ValidateOnly -or $ValidateLive){$ValidationAccount}else{'personal'}
$script:exiting=$false
$script:request=$null
$script:aiRequest=$null
$handler=New-Object System.Net.Http.HttpClientHandler
$handler.UseProxy=$false
$script:client=[System.Net.Http.HttpClient]::new($handler)
$script:client.Timeout=[TimeSpan]::FromSeconds(20)
$script:origin='http://127.0.0.1:8000'
[xml]$xaml=@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Title="Postmark" Width="420" Height="610" ResizeMode="NoResize" WindowStartupLocation="CenterScreen" Background="#FAF9F6" FontFamily="Segoe UI" ShowInTaskbar="False">
 <Window.Resources>
  <Style TargetType="Button"><Setter Property="Padding" Value="12,10"/><Setter Property="Margin" Value="0,0,7,7"/><Setter Property="Background" Value="White"/><Setter Property="BorderBrush" Value="#171717"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Cursor" Value="Hand"/><Setter Property="FontSize" Value="12"/><Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button"><Border Name="ButtonFrame" CornerRadius="6" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" Padding="{TemplateBinding Padding}"><ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="ButtonFrame" Property="Opacity" Value="0.82"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter TargetName="ButtonFrame" Property="Opacity" Value="0.45"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
  <Style TargetType="TextBlock"><Setter Property="Foreground" Value="#242321"/><Setter Property="TextWrapping" Value="Wrap"/></Style>
 </Window.Resources>
 <Grid Margin="22"><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="16"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
 <StackPanel><TextBlock Text="Postmark" FontSize="27" FontWeight="Bold"/><ComboBox Name="Account" Height="32" SelectedIndex="0"><ComboBoxItem Content="Personal Gmail"/><ComboBoxItem Content="University Gmail"/></ComboBox></StackPanel>
 <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled"><StackPanel>
 <Border Background="#E6FFFFFF" BorderBrush="#171717" BorderThickness="2" CornerRadius="13" Padding="14" Margin="0,0,4,8"><StackPanel>
 <TextBlock Name="AccountState" Text="● Account: unknown" FontSize="11" Foreground="#85817A" Margin="0,0,0,7"/><TextBlock Name="AIStatus" Text="Connecting…" FontSize="16" FontWeight="SemiBold"/><TextBlock Name="AIHint" Foreground="#777167" FontSize="12" Margin="0,4,0,10"/>
 <TextBlock Name="LastAI" Foreground="#777167" FontSize="11" Margin="0,8,0,2"/><TextBlock Name="EngineState" FontSize="11" Foreground="#777167" Margin="0,0,0,6"/><ProgressBar Name="OrganizationProgress" Height="8" Minimum="0" Maximum="100" Foreground="#52775F" Background="#E7E5DF" BorderThickness="0"><ProgressBar.Template><ControlTemplate TargetType="ProgressBar"><Grid Name="PART_Track" Background="{TemplateBinding Background}" ClipToBounds="True"><Border Name="PART_Indicator" HorizontalAlignment="Left" Background="{TemplateBinding Foreground}" CornerRadius="3"/></Grid></ControlTemplate></ProgressBar.Template></ProgressBar><TextBlock Name="ProgressCounts" FontSize="11" Foreground="#777167" Margin="0,5,0,9"/><Grid><Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions><StackPanel><TextBlock Text="New inbox mail" Foreground="#777167" FontSize="11"/><TextBlock Name="NewMail" Text="—" FontSize="24" FontWeight="Bold"/></StackPanel><StackPanel Grid.Column="1"><TextBlock Text="Awaiting analysis" Foreground="#777167" FontSize="11"/><TextBlock Name="Awaiting" Text="—" FontSize="24" FontWeight="Bold"/></StackPanel></Grid>
 <Separator Margin="0,8,0,8"/><TextBlock Text="Older mail" FontWeight="SemiBold"/><TextBlock Name="SyncState" Text="Checking…" FontSize="12" Margin="0,5,0,4"/><TextBlock Name="Coverage" Foreground="#777167" FontSize="12"/><TextBlock Name="Cleanup" FontSize="12"/>
 </StackPanel></Border>
 </StackPanel></ScrollViewer>
 <StackPanel Grid.Row="3"><WrapPanel><Button Name="Inbox" Content="Open inbox" Background="#171717" Foreground="White"/><Button Name="Other" Content="View Other"/><Button Name="Review" Content="Needs Review"/><Button Name="Resume" Content="Resume sync" IsEnabled="False"/><Button Name="Providers" Content="AI settings"/><Button Name="History" Content="Sync &amp; History"/></WrapPanel>
 <TextBlock Name="LastRefresh" FontSize="10" Foreground="#85817A" Margin="0,2,0,4"/><TextBlock Name="Notice" FontSize="11" Foreground="#777167" Margin="0,2,0,4"/>
 </StackPanel>
 <DockPanel Grid.Row="4" Margin="0,6,0,0"><Button Name="Refresh" Content="Refresh"/><TextBlock Text="Closing this panel keeps workers running." FontSize="10" Foreground="#777167" VerticalAlignment="Center"/></DockPanel>
 </Grid>
</Window>
'@
$reader=New-Object System.Xml.XmlNodeReader($xaml)
$window=[Windows.Markup.XamlReader]::Load($reader)
$nodes=@{}
foreach($name in @('AccountState','EngineState','OrganizationProgress','ProgressCounts','LastRefresh','Account','AIStatus','AIHint','NewMail','Awaiting','SyncState','Coverage','LastAI','Cleanup','Inbox','Other','Review','Resume','Providers','History','Notice','Refresh')){$nodes[$name]=$window.FindName($name)}
$nodes.Account.SelectedIndex=if($script:account -eq 'university'){1}else{0}
$iconPath=Join-Path $script:base 'email-agent.ico'
if(Test-Path $iconPath){$window.Icon=[System.Windows.Media.Imaging.BitmapFrame]::Create([Uri]$iconPath)}
function Open-Postmark([string]$path){Start-Process ($script:origin+$path+'?account='+$script:account)}
function Refresh-Status {
 if($script:request -and -not $script:request.IsCompleted){return}
 $script:requestAccount=$script:account
 $script:request=$script:client.GetAsync($script:origin+'/api/control/'+$script:account)

}
function Render-Control($data){
 $script:currentActivity=$data.activity
 $script:lastRefresh=[DateTimeOffset]::Now
 $nodes.LastRefresh.Text=if($data.observed_at){'Verified '+[DateTimeOffset]::FromUnixTimeSeconds([long]$data.observed_at).ToLocalTime().ToString('HH:mm:ss')}else{'Refreshed '+$script:lastRefresh.ToString('HH:mm:ss')}
 $nodes.AccountState.Text='● Account: '+[string]$data.account_connection
 $nodes.AccountState.Foreground=if($data.account_connection -eq 'Connected'){'#52775F'}elseif($data.account_connection -eq 'Disconnected'){'#A75B57'}elseif($data.account_connection -eq 'Reconnecting'){'#AA8750'}else{'#85817A'}
 $p=$data.progress
 $nodes.OrganizationProgress.Value=if($p.total -gt 0){100.0*$p.organized/$p.total}else{0}
 $nodes.ProgressCounts.Text=('{0} organized / {1} known · {2} waiting' -f $p.organized,$p.total,($p.waiting_gmail+$p.waiting_ai))
 Render-AI $data.engine $data.activity $data.progress
 $nodes.NewMail.Text=[string]$data.new_mail
 $nodes.Awaiting.Text=[string]$data.awaiting_analysis
 $nodes.SyncState.Text=[string]$data.sync.state
 $nodes.Coverage.Text=('{0} bodies available · {1} remaining' -f $data.sync.bodies_available,$data.sync.bodies_remaining)
 $nodes.Cleanup.Text=('{0} cleanup candidates to review' -f $data.cleanup_candidates)
 $nodes.Resume.IsEnabled=-not $data.sync.enabled
 $nodes.Resume.Content=if($data.sync.running){'Syncing'}elseif($data.sync.enabled){'Sync queued'}else{'Resume sync'}
 if($data.stale){$nodes.Notice.Text='Showing last verified progress; retrying.'}elseif($nodes.Notice.Text -in @('Showing last verified progress; retrying.','Refresh delayed; showing last verified status.')){$nodes.Notice.Text=''}
 if($data.latest_classification){$time=[DateTimeOffset]::Parse($data.latest_classification.updated_at.Replace(' ','T')+'Z').ToLocalTime();$nodes.LastAI.Text='Latest AI classification: '+$time.ToString('dd MMM HH:mm')}else{$nodes.LastAI.Text='No successful AI classification yet.'}
}
function Render-AI($ai,$activity,$progress){
 $nodes.EngineState.Text=('Local: {0} · Cloud routes: {1}' -f $ai.local,$ai.cloud_state)
 if($ai.local_throttled){$nodes.EngineState.Text+=' · Local queued: memory'}
 $active=@($activity.active | Where-Object {($_.lease_until -gt [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -or (-not $_.lease_until -and $_.started_at -gt ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-120))})
 $script:currentActivity=$activity
 if($active.Count -gt 0){
  $r=$active[0];$elapsed=[TimeSpan]::FromSeconds([Math]::Max(0,[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$r.started_at))
  $nodes.AIStatus.Text='● AI working'
  $nodes.AIStatus.Foreground='#52775F'
  if($script:dotState -ne 'active'){$pulse=New-Object Windows.Media.Animation.DoubleAnimation;$pulse.From=0.55;$pulse.To=1;$pulse.Duration=[Windows.Duration]::new([TimeSpan]::FromSeconds(1));$pulse.AutoReverse=$true;$pulse.RepeatBehavior=[Windows.Media.Animation.RepeatBehavior]::Forever;$nodes.AIStatus.BeginAnimation([Windows.UIElement]::OpacityProperty,$pulse);$script:dotState='active'}
  $nodes.AIHint.Text=('{0} · {1} · {2:mm\:ss}' -f $r.provider,$r.model,$elapsed)
  if($r.routing_reason){$nodes.AIHint.ToolTip=$r.routing_reason}
 }else{
  if($script:dotState -eq 'active'){$nodes.AIStatus.BeginAnimation([Windows.UIElement]::OpacityProperty,$null);$script:dotState='idle'}
  $nodes.AIStatus.Text=if($activity.state -in @('Disabled','Paused')){'● AI paused'}elseif($progress.waiting_ai -gt 0 -or $progress.waiting_gmail -gt 0){'● AI queued'}else{'● AI idle'}
  $nodes.AIStatus.Foreground=if($activity.state -in @('Waiting','Queued','Waiting for capacity')){'#AA8750'}elseif($activity.state -eq 'Failed'){'#A75B57'}else{'#85817A'}
  $nodes.AIHint.Text=('{0} waiting for AI · {1} completed last hour' -f $progress.waiting_ai,$activity.completed_1h)
 }
 if($activity.last_successful_inference){$nodes.AIStatus.ToolTip='Last successful inference: '+[DateTimeOffset]::FromUnixTimeSeconds([long]$activity.last_successful_inference).ToLocalTime().ToString('dd MMM HH:mm:ss')}
 $script:lastEngine=$ai;$script:lastProgress=$progress
}
function Poll-Status {
 if($script:aiRequest -and $script:aiRequest.IsCompleted){
  try{$response=$script:aiRequest.GetAwaiter().GetResult();$response.EnsureSuccessStatusCode()|Out-Null;$activity=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult()|ConvertFrom-Json;if($script:aiAccount -eq $script:account -and $script:lastEngine){Render-AI $script:lastEngine $activity $script:lastProgress};$response.Dispose()}catch{}finally{$script:aiRequest=$null}
 }
 if($window.IsVisible -and -not $script:aiRequest -and ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-[long]$script:activityTick) -ge 5){$script:activityTick=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();$script:aiAccount=$script:account;$script:aiRequest=$script:client.GetAsync($script:origin+'/api/control/'+$script:account+'?activity_only=true')}

 if($script:currentActivity -and $script:lastEngine -and $script:lastProgress -and $script:elapsedTick -ne [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()){$script:elapsedTick=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();Render-AI $script:lastEngine $script:currentActivity $script:lastProgress}
 if($script:request -and $script:request.IsCompleted){
  try {
   $response=$script:request.GetAwaiter().GetResult();$response.EnsureSuccessStatusCode()|Out-Null
   $data=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult()|ConvertFrom-Json
   if($script:requestAccount -eq $script:account){
    Render-Control $data
   }
   $response.Dispose()
  } catch {$nodes.Notice.Text='Refresh delayed; showing last verified status.';$nodes.Resume.IsEnabled=$false}
  finally {$script:request=$null;if($script:requestAccount -ne $script:account){Refresh-Status}}
 }
 if($script:controlRequest -and $script:controlRequest.IsCompleted){
  try{$r=$script:controlRequest.GetAwaiter().GetResult();$r.EnsureSuccessStatusCode()|Out-Null;if($script:controlAccount -eq $script:account){$nodes.Notice.Text='Sync resumes through the existing worker.'};$r.Dispose()}catch{$nodes.Notice.Text='Could not resume. Open Sync & History to check.'}finally{$script:controlRequest=$null;Refresh-Status}
 }
}
$nodes.Account.Add_SelectionChanged({$script:currentActivity=$null;$script:lastEngine=$null;$script:lastProgress=$null;$nodes.OrganizationProgress.Value=0;$nodes.AccountState.Text='● Account: unknown';$nodes.AccountState.Foreground='#85817A';$script:account=if($nodes.Account.SelectedIndex -eq 1){'university'}else{'personal'};foreach($name in @('NewMail','Awaiting','Coverage','LastAI','Cleanup')){$nodes[$name].Text='—'};$nodes.Resume.IsEnabled=$false;$nodes.AIStatus.Text='Checking account…';$nodes.AIHint.Text='';$nodes.SyncState.Text='Checking…';Refresh-Status})
$nodes.Inbox.Add_Click({Open-Postmark '/inbox'})
$nodes.Other.Add_Click({Open-Postmark '/categories/other'})
$nodes.Review.Add_Click({Open-Postmark '/review'})
$nodes.Providers.Add_Click({Open-Postmark '/settings'})
$nodes.History.Add_Click({Open-Postmark '/sync-history'})
$nodes.Refresh.Add_Click({Refresh-Status})
$nodes.Resume.Add_Click({
 if($script:controlRequest){return}
 $nodes.Resume.IsEnabled=$false
 $message=New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post,($script:origin+'/api/control/'+$script:account+'/sync'))
 $message.Headers.Add('X-Email-Agent-Action','message-management')
 $message.Content=New-Object System.Net.Http.StringContent('{"action":"resume"}',[Text.Encoding]::UTF8,'application/json')
 $script:controlAccount=$script:account
 $script:controlRequest=$script:client.SendAsync($message)
 $nodes.Notice.Text='Requesting resume…'
})
if($ValidateOnly -or $ValidateLive){
 $ErrorActionPreference='Stop'
 if($ValidateLive){
  $data=$script:client.GetStringAsync($script:origin+'/api/control/'+$script:account).GetAwaiter().GetResult()|ConvertFrom-Json
  Render-Control $data
  $verifiedCounts=$nodes.ProgressCounts.Text
  $failed=[System.Threading.Tasks.TaskCompletionSource[System.Net.Http.HttpResponseMessage]]::new()
  $failed.SetException([System.Net.Http.HttpRequestException]::new('Synthetic local refresh failure'))
  $script:requestAccount=$script:account;$script:request=$failed.Task
  Poll-Status
  if($nodes.ProgressCounts.Text -ne $verifiedCounts){throw 'Transient failure discarded verified progress'}
  Refresh-Status;$script:request.GetAwaiter().GetResult()|Out-Null;Poll-Status
  if($nodes.Notice.Text -eq 'Refresh delayed; showing last verified status.'){throw 'Refresh warning did not recover'}

 }
 $window.ShowActivated=$false;$window.Left=-10000;$window.Top=-10000
 $window.Show();$window.UpdateLayout();$window.Dispatcher.Invoke([Action]{},[Windows.Threading.DispatcherPriority]::Render);$root=$window.Content
 $visual=New-Object Windows.Media.DrawingVisual
 $context=$visual.RenderOpen()
 $context.DrawRectangle($window.Background,$null,[Windows.Rect]::new(0,0,420,610))
 $brush=New-Object Windows.Media.VisualBrush($root)
 $context.DrawRectangle($brush,$null,[Windows.Rect]::new(22,22,$root.ActualWidth,$root.ActualHeight))
 $context.Close()
 $image=New-Object Windows.Media.Imaging.RenderTargetBitmap(420,610,96,96,[Windows.Media.PixelFormats]::Pbgra32)
 $image.Render($visual);$encoder=New-Object Windows.Media.Imaging.PngBitmapEncoder;$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($image))
 $file=[IO.File]::Create((Join-Path $script:base 'mini-preview.png'));try{$encoder.Save($file)}finally{$file.Dispose()}
 if($ValidateLive){Write-Output 'PASS native WPF rendered at420x610; same-window transient failure retained progress and real GET recovered; no AI/Gmail calls.'}else{Write-Output 'PASS native WPF XAML loaded and rendered at420x610; no HTTP/AI/Gmail calls.'}
 $window.Close();$script:client.Dispose();exit
}
$created=$false
$script:mutex=[Threading.Mutex]::new($true,'Local\PostmarkControlCenter',[ref]$created)
if(-not $created){$script:mutex.Dispose();$script:client.Dispose();exit}
$tray=New-Object System.Windows.Forms.NotifyIcon
$tray.Icon=if(Test-Path $iconPath){New-Object System.Drawing.Icon($iconPath)}else{[System.Drawing.SystemIcons]::Application}
$tray.Text='Postmark';$tray.Visible=$true
$menu=New-Object System.Windows.Forms.ContextMenuStrip
$show=$menu.Items.Add('Open Postmark panel');$activity=$menu.Items.Add('AI activity and usage');$activity.Add_Click({Open-Postmark '/ai-activity'});$exit=$menu.Items.Add('Exit panel');$tray.ContextMenuStrip=$menu
$show.Add_Click({$window.Show();$window.Activate();Refresh-Status})
$tray.Add_MouseClick({if($_.Button -eq [System.Windows.Forms.MouseButtons]::Left){$window.Show();$window.Activate();Refresh-Status}})
$exit.Add_Click({$script:exiting=$true;$window.Close();$app.Shutdown()})
$window.Add_Closing({if(-not $script:exiting){$_.Cancel=$true;$window.Hide()}})
$poll=New-Object Windows.Threading.DispatcherTimer;$poll.Interval=[TimeSpan]::FromMilliseconds(250);$poll.Add_Tick({Poll-Status});$poll.Start()
$timer=New-Object Windows.Threading.DispatcherTimer;$timer.Interval=[TimeSpan]::FromSeconds(10);$timer.Add_Tick({if($window.IsVisible){Refresh-Status}});$timer.Start()
$app=New-Object Windows.Application;$app.ShutdownMode=[Windows.ShutdownMode]::OnExplicitShutdown
Refresh-Status
try{$null=$app.Run($window)}finally{$poll.Stop();$timer.Stop();$tray.Visible=$false;$tray.Dispose();$script:mutex.ReleaseMutex();$script:mutex.Dispose();$script:client.Dispose()}
